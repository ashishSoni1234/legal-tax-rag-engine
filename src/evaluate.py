"""
src/evaluate.py — Milestone 4: RAG Evaluation Pipeline
=======================================================
Evaluates the full RAG pipeline against Golden_Set.xlsx:

1. Retrieval Accuracy  — does the top-1 / top-3 retrieved doc match the ground truth?
2. Faithfulness score  — LLM-as-judge (Claude) rates 1-5 factual consistency.

Outputs:
    /outputs/evaluation_report.json
    /docs/Evaluation_Report.md

Usage:
    python src/evaluate.py
    python src/evaluate.py --golden Golden_Set.xlsx --top_k 5 --max_rows 20
"""

import os
import sys
import io
import re
import json
import time
import logging
import argparse
from pathlib import Path
from typing import Dict, Any, List, Optional
from collections import defaultdict
from openai import OpenAI

# ─── UTF-8 fix (only when run directly, not under pytest) ────────────────────

if __name__ == "__main__" and hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import pandas as pd
from dotenv import load_dotenv

load_dotenv(override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# ─── Path setup ───────────────────────────────────────────────────────────────
PROJECT_ROOT  = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

CHUNKS_JSON   = PROJECT_ROOT / "outputs" / "parsed_chunks.json"
GRAPH_JSON    = PROJECT_ROOT / "outputs" / "citation_graph.json"
GOLDEN_XLSX   = PROJECT_ROOT / "Golden_Set.xlsx"
REPORT_JSON   = PROJECT_ROOT / "outputs" / "evaluation_report.json"
REPORT_MD     = PROJECT_ROOT / "docs"    / "Evaluation_Report.md"

LLM_MODEL    = os.getenv("LLM_MODEL",    "llama-3.3-70b-versatile")
LLM_API_KEY  = os.getenv("LLM_API_KEY",  "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")

# ─── Retry / rate-limit helpers ──────────────────────────────────────────────

def _call_llm(client, system: str, user_msg: str, max_tokens: int = 2048,
              retries: int = 3) -> str:
    """Call Llama via OpenAI client with retry on rate-limit errors."""
    import openai
    for attempt in range(retries):
        try:
            resp = client.chat.completions.create(
                model=LLM_MODEL,
                temperature=0,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_msg}
                ],
            )
            return resp.choices[0].message.content
        except openai.RateLimitError:
            wait = 2 ** attempt * 5
            logger.warning("Rate limit hit — waiting %ds (attempt %d/%d)", wait, attempt+1, retries)
            time.sleep(wait)
        except Exception as exc:
            logger.error("LLM call failed: %s", exc)
            return ""
    return ""


# ─── Retrieval helpers ────────────────────────────────────────────────────────

def _normalise_doc_name(name: str) -> str:
    """
    Normalise a document name for comparison:
    strip extension, lower-case, collapse whitespace/punctuation.
    """
    name = str(name)
    name = re.sub(r"\.pdf$", "", name, flags=re.IGNORECASE)
    name = name.lower()
    name = re.sub(r"[^a-z0-9]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _docs_match(retrieved_name: str, golden_name: str) -> bool:
    """True if the retrieved doc name is sufficiently similar to the golden name."""
    r = _normalise_doc_name(retrieved_name)
    g = _normalise_doc_name(golden_name)

    if r == g:
        return True
    if r in g or g in r:
        return True
    # Token overlap ≥ 60%
    r_toks = set(r.split())
    g_toks = set(g.split())
    if not g_toks:
        return False
    overlap = len(r_toks & g_toks) / len(g_toks)
    return overlap >= 0.6


def _build_context(results: List[Dict]) -> str:
    lines = []
    for i, r in enumerate(results, 1):
        okf = r.get("okf_context", {})
        src = okf.get("source", r.get("doc_name", ""))
        pg  = okf.get("page",   r.get("page_number", ""))
        txt = okf.get("content", r.get("text", ""))
        lines.append(f"[CONTEXT {i}] Source: {src}, Page: {pg}")
        lines.append(txt)
        lines.append("")
    return "\n".join(lines)


# ─── Main evaluation ──────────────────────────────────────────────────────────

def evaluate(args) -> None:
    from search    import build_index, hybrid_search
    from graph_rag import load_graph, build_citation_graph, save_graph, enrich_results_with_graph

    # ── Load golden set ───────────────────────────────────────────────────────
    golden_path = Path(args.golden)
    if not golden_path.exists():
        raise FileNotFoundError(f"Golden set not found: {golden_path}")

    df = pd.read_excel(golden_path, dtype=str).fillna("")
    logger.info("Golden set loaded: %d rows", len(df))

    # Column aliases (handle variations in column naming)
    col_map = {}
    for col in df.columns:
        key = col.strip().lower().replace(" ", "_").replace("/", "_")
        col_map[key] = col

    def get_col(key: str, fallback: str = "") -> pd.Series:
        if key in col_map:
            return df[col_map[key]]
        return pd.Series([fallback] * len(df))

    ids           = get_col("id")
    categories    = get_col("category")
    queries       = get_col("query")
    ground_truths = get_col("ground_truth_answer")
    source_docs   = get_col("source_document")
    difficulties  = get_col("difficulty", "Medium")

    # Apply row limit
    if args.max_rows and args.max_rows > 0:
        n = min(args.max_rows, len(df))
        logger.info("Evaluating first %d rows (out of %d)", n, len(df))
        df = df.iloc[:n]
        ids           = ids[:n]
        categories    = categories[:n]
        queries       = queries[:n]
        ground_truths = ground_truths[:n]
        source_docs   = source_docs[:n]
        difficulties  = difficulties[:n]

    # ── Load chunks + build index ─────────────────────────────────────────────
    if not CHUNKS_JSON.exists():
        raise FileNotFoundError(f"Run parser.py first — {CHUNKS_JSON} not found.")

    with open(CHUNKS_JSON, encoding="utf-8") as f:
        data   = json.load(f)
    chunks = data["chunks"]
    logger.info("Loaded %d chunks.", len(chunks))

    lookup: Dict[str, list] = defaultdict(list)
    for c in chunks:
        lookup[c["doc_name"]].append(c)

    # Graph
    if GRAPH_JSON.exists():
        try:
            G = load_graph(str(GRAPH_JSON))
        except Exception:
            G = build_citation_graph(chunks)
            save_graph(G, str(GRAPH_JSON))
    else:
        G = build_citation_graph(chunks)
        save_graph(G, str(GRAPH_JSON))

    build_index()
    logger.info("Search index ready.")

    # LLM client
    client = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)

    # ── Evaluation loop ───────────────────────────────────────────────────────
    results_rows: List[Dict] = []
    top_k = args.top_k

    rag_system_prompt = """You are a legal research assistant specializing in US tax law.
Answer ONLY using the provided context chunks. After each claim, cite: [Source: doc_name, Page N].
If not in context, respond exactly: "I could not find sufficient information in the provided documents to answer this question."
Do NOT use external knowledge."""

    judge_system = """You are an expert legal AI evaluator. Rate the faithfulness of the system answer.
Respond with ONLY a number 1-5 followed by a period and a one-sentence reason.
Scale: 1=contains hallucinations, 2=significant errors, 3=mostly correct with some gaps, 4=mostly faithful minor omissions, 5=fully faithful no hallucinations."""

    for i, (row_id, category, query, gt_answer, source_doc, difficulty) in enumerate(
        zip(ids, categories, queries, ground_truths, source_docs, difficulties)
    ):
        query     = str(query).strip()
        gt_answer = str(gt_answer).strip()
        source_doc = str(source_doc).strip()

        if not query:
            continue

        logger.info("[%d/%d] ID=%s  Q=%s…", i+1, len(df), row_id, query[:60])

        row: Dict[str, Any] = {
            "id"           : str(row_id),
            "category"     : str(category),
            "query"        : query,
            "ground_truth" : gt_answer,
            "source_doc"   : source_doc,
            "difficulty"   : str(difficulty),
        }

        # ── Retrieval ─────────────────────────────────────────────────────────
        try:
            retrieved = hybrid_search(query, top_k=top_k)
            if retrieved:
                retrieved = enrich_results_with_graph(retrieved, G, dict(lookup))
        except Exception as exc:
            logger.error("Search error for ID=%s: %s", row_id, exc)
            retrieved = []

        retrieved_docs = [r.get("doc_name", "") for r in retrieved]

        # Retrieval accuracy
        top1_match  = bool(retrieved_docs) and _docs_match(retrieved_docs[0], source_doc)
        top3_match  = any(_docs_match(d, source_doc) for d in retrieved_docs[:3])
        top5_match  = any(_docs_match(d, source_doc) for d in retrieved_docs[:5])

        row["retrieved_docs"] = retrieved_docs[:5]
        row["top1_match"]     = top1_match
        row["top3_match"]     = top3_match
        row["top5_match"]     = top5_match

        # ── LLM Answer ───────────────────────────────────────────────────────
        system_answer = ""
        if client and retrieved:
            context_block = _build_context(retrieved)
            user_msg      = f"Context:\n\n{context_block}\n\n---\nQuestion: {query}"
            system_answer = _call_llm(client, rag_system_prompt, user_msg)
            time.sleep(0.3)   # gentle rate-limiting
        elif not retrieved:
            system_answer = "I could not find sufficient information in the provided documents to answer this question."

        row["system_answer"] = system_answer

        # ── Faithfulness scoring ──────────────────────────────────────────────
        faithfulness_score  = None
        faithfulness_reason = ""

        if client and gt_answer and system_answer:
            judge_prompt = (
                f"Ground Truth Answer:\n{gt_answer}\n\n"
                f"System Generated Answer:\n{system_answer}\n\n"
                "Rate 1-5 whether the system answer is factually consistent with the ground truth "
                "and does NOT contain hallucinated information not present in the ground truth."
            )
            judge_resp = _call_llm(client, judge_system, judge_prompt, max_tokens=100)
            m = re.match(r"([1-5])[.\s]+(.*)", judge_resp.strip(), re.DOTALL)
            if m:
                faithfulness_score  = int(m.group(1))
                faithfulness_reason = m.group(2).strip()[:200]
            else:
                # Try just extracting a digit
                dm = re.search(r"[1-5]", judge_resp)
                if dm:
                    faithfulness_score = int(dm.group(0))
            time.sleep(0.3)

        row["faithfulness_score"]  = faithfulness_score
        row["faithfulness_reason"] = faithfulness_reason

        results_rows.append(row)
        logger.info(
            "  → top1=%s top3=%s faith=%s",
            "✓" if top1_match else "✗",
            "✓" if top3_match else "✗",
            faithfulness_score if faithfulness_score else "N/A",
        )

    # ── Compute aggregate metrics ─────────────────────────────────────────────

    def _pct(rows: List[Dict], key: str) -> float:
        vals = [r[key] for r in rows if isinstance(r.get(key), bool)]
        return round(100.0 * sum(vals) / len(vals), 1) if vals else 0.0

    def _avg(rows: List[Dict], key: str) -> Optional[float]:
        vals = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
        return round(sum(vals) / len(vals), 2) if vals else None

    overall = {
        "total_evaluated"      : len(results_rows),
        "retrieval_top1_pct"   : _pct(results_rows, "top1_match"),
        "retrieval_top3_pct"   : _pct(results_rows, "top3_match"),
        "retrieval_top5_pct"   : _pct(results_rows, "top5_match"),
        "avg_faithfulness"     : _avg(results_rows, "faithfulness_score"),
    }

    # By category
    by_category: Dict[str, Dict] = {}
    cats = set(r["category"] for r in results_rows)
    for cat in cats:
        cat_rows = [r for r in results_rows if r["category"] == cat]
        by_category[cat] = {
            "n"               : len(cat_rows),
            "retrieval_top1"  : _pct(cat_rows, "top1_match"),
            "retrieval_top3"  : _pct(cat_rows, "top3_match"),
            "avg_faithfulness": _avg(cat_rows, "faithfulness_score"),
        }

    # By difficulty
    by_difficulty: Dict[str, Dict] = {}
    diffs = set(r["difficulty"] for r in results_rows)
    for diff in diffs:
        diff_rows = [r for r in results_rows if r["difficulty"] == diff]
        by_difficulty[diff] = {
            "n"               : len(diff_rows),
            "retrieval_top1"  : _pct(diff_rows, "top1_match"),
            "retrieval_top3"  : _pct(diff_rows, "top3_match"),
            "avg_faithfulness": _avg(diff_rows, "faithfulness_score"),
        }

    # Worst 10 by faithfulness then retrieval
    scored = [r for r in results_rows if r.get("faithfulness_score")]
    worst10 = sorted(
        scored,
        key=lambda r: (r.get("faithfulness_score", 5), int(r.get("top1_match", True)))
    )[:10]

    # ── Save JSON report ──────────────────────────────────────────────────────
    report = {
        "summary"    : overall,
        "by_category": by_category,
        "by_difficulty": by_difficulty,
        "worst_10"   : [
            {"id": r["id"], "category": r["category"], "difficulty": r["difficulty"],
             "query": r["query"], "faithfulness": r.get("faithfulness_score"),
             "top1_match": r.get("top1_match"), "reason": r.get("faithfulness_reason","")}
            for r in worst10
        ],
        "detailed_results": results_rows,
    }

    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_JSON, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    logger.info("JSON report saved → %s", REPORT_JSON)

    # ── Save Markdown report ──────────────────────────────────────────────────
    _write_markdown_report(report)
    logger.info("Markdown report saved → %s", REPORT_MD)
    logger.info("=" * 60)
    logger.info("EVALUATION SUMMARY")
    logger.info("  Top-1 Retrieval Accuracy : %.1f%%", overall["retrieval_top1_pct"])
    logger.info("  Top-3 Retrieval Accuracy : %.1f%%", overall["retrieval_top3_pct"])
    logger.info("  Avg Faithfulness (1-5)   : %s",
                f"{overall['avg_faithfulness']:.2f}" if overall['avg_faithfulness'] else "N/A")


def _write_markdown_report(report: Dict) -> None:
    s   = report["summary"]
    now = time.strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        "# Legal & Tax RAG System — Evaluation Report",
        "",
        f"*Generated: {now}*",
        "",
        "---",
        "",
        "## Overall Performance",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Total queries evaluated | {s['total_evaluated']} |",
        f"| **Retrieval Top-1 Accuracy** | **{s['retrieval_top1_pct']}%** |",
        f"| **Retrieval Top-3 Accuracy** | **{s['retrieval_top3_pct']}%** |",
        f"| **Retrieval Top-5 Accuracy** | **{s['retrieval_top5_pct']}%** |",
        f"| Average Faithfulness (1–5) | {s['avg_faithfulness'] or 'N/A'} |",
        "",
        "> [!NOTE]",
        "> Faithfulness is scored 1–5 by Claude (LLM-as-judge). 5 = fully faithful, 1 = hallucinated.",
        "",
        "---",
        "",
        "## Breakdown by Category",
        "",
        "| Category | N | Top-1 Acc | Top-3 Acc | Avg Faith |",
        "|----------|---|-----------|-----------|-----------|",
    ]
    for cat, m in sorted(report["by_category"].items()):
        faith = f"{m['avg_faithfulness']:.2f}" if m["avg_faithfulness"] else "N/A"
        lines.append(f"| {cat} | {m['n']} | {m['retrieval_top1']}% | {m['retrieval_top3']}% | {faith} |")

    lines += [
        "",
        "---",
        "",
        "## Breakdown by Difficulty",
        "",
        "| Difficulty | N | Top-1 Acc | Top-3 Acc | Avg Faith |",
        "|------------|---|-----------|-----------|-----------|",
    ]
    for diff, m in sorted(report["by_difficulty"].items()):
        faith = f"{m['avg_faithfulness']:.2f}" if m["avg_faithfulness"] else "N/A"
        lines.append(f"| {diff} | {m['n']} | {m['retrieval_top1']}% | {m['retrieval_top3']}% | {faith} |")

    lines += [
        "",
        "---",
        "",
        "## Worst 10 Performing Queries (for manual review)",
        "",
        "| ID | Category | Difficulty | Faithfulness | Top-1 | Query |",
        "|----|----------|------------|--------------|-------|-------|",
    ]
    for w in report.get("worst_10", []):
        t1 = "✓" if w.get("top1_match") else "✗"
        f  = w.get("faithfulness", "N/A")
        q  = str(w.get("query", ""))[:80].replace("|", "\\|")
        lines.append(f"| {w['id']} | {w['category']} | {w['difficulty']} | {f} | {t1} | {q} |")

    lines += [
        "",
        "---",
        "",
        "## Notes",
        "- Retrieval accuracy measures whether the ground truth source document appears in the system's retrieved results.",
        "- Fuzzy matching (60% token overlap) is used for document name comparison.",
        "- Graph RAG enrichment adds Act documents cited by retrieved Judgment chunks.",
        "- Faithfulness is evaluated by Llama 3.1 via OpenAI client at temperature=0.",
        "",
        "*End of Report*",
    ]

    REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Legal & Tax RAG — Evaluation")
    ap.add_argument("--golden",   default=str(GOLDEN_XLSX), help="Path to Golden_Set.xlsx")
    ap.add_argument("--top_k",    type=int, default=5,      help="Chunks to retrieve per query")
    ap.add_argument("--max_rows", type=int, default=0,      help="Limit rows (0=all)")
    args = ap.parse_args()
    evaluate(args)
