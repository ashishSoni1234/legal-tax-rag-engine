"""
src/parser.py — Milestone 1: PDF Ingestion Pipeline
=====================================================
Reads every PDF in /data/{acts,judgement,pov,Tax}, extracts text page-by-page
using PyMuPDF, chunks into ~600-token segments with ~75-token overlap, and
saves structured JSON to /outputs/parsed_chunks.json.

Chunk metadata schema:
    {
        "chunk_id"     : "acts__26_U_S__Code__61__0",
        "doc_name"     : "26 U.S. Code § 61 - Gross income defined ...",
        "doc_category" : "Acts",
        "page_number"  : 1,
        "text"         : "...",
        "token_count"  : 587,
        "section"      : "§ 61"
    }

Usage:
    python src/parser.py
    python src/parser.py --data_dir data --output outputs/parsed_chunks.json
"""

import os
import re
import sys
import io
import json
import logging
import argparse
from pathlib import Path
from typing import List, Dict, Any

# ─── Fix Windows console encoding (only when run directly, not under pytest) ──
# This prevents UnicodeEncodeError when logging filenames with § or other chars.

if __name__ == "__main__" and hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")



import fitz          # PyMuPDF
import tiktoken

# ─── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# ─── Constants ───────────────────────────────────────────────────────────────
CATEGORY_MAP: Dict[str, str] = {
    "acts":      "Acts",
    "judgement": "Judgments",
    "pov":       "POV",
    "tax":       "Tax Docs",   # actual folder is "Tax"
}

CHUNK_TOKENS    = 600
OVERLAP_TOKENS  = 75
MIN_CHUNK_CHARS = 50
TOKENIZER       = tiktoken.get_encoding("cl100k_base")


# ─── Helpers ─────────────────────────────────────────────────────────────────

def count_tokens(text: str) -> int:
    return len(TOKENIZER.encode(text))


def clean_text(raw: str) -> str:
    text = raw.replace("\f", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_section_header(text: str) -> str:
    """Pull the first plausible section/clause reference from chunk text."""
    patterns = [
        r"(?:IRC\s*|26\s+U\.S\.C?\.?\s*)?§\s*\d+[\w.()\-]*",
        r"Section\s+\d+[\w.()\-]*",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(0)[:80]
    return ""


def chunk_page_tokens(page_text: str) -> List[str]:
    """Slide a token window over *page_text* and return overlapping chunk strings."""
    tokens = TOKENIZER.encode(page_text)
    if not tokens:
        return []
    step   = max(1, CHUNK_TOKENS - OVERLAP_TOKENS)
    chunks = []
    for start in range(0, len(tokens), step):
        window = tokens[start : start + CHUNK_TOKENS]
        if not window:
            break
        chunk_str = TOKENIZER.decode(window)
        if len(chunk_str.strip()) >= MIN_CHUNK_CHARS:
            chunks.append(chunk_str.strip())
    return chunks


def _safe_name(text: str) -> str:
    """Convert text to a filesystem-safe ASCII string."""
    return re.sub(r"[^A-Za-z0-9_]", "_", text)[:60]


# ─── Core pipeline ───────────────────────────────────────────────────────────

def extract_chunks_from_pdf(pdf_path: Path, category: str) -> List[Dict[str, Any]]:
    """
    Parse a single PDF and return a flat list of chunk dicts.
    Returns an empty list on any error — never raises.
    """
    doc_name    = pdf_path.stem
    all_chunks: List[Dict[str, Any]] = []
    chunk_index = 0
    doc         = None

    try:
        doc = fitz.open(str(pdf_path))
        if doc.page_count == 0:
            logger.warning("Empty PDF (0 pages): %s", doc_name[:80])
            return []

        page_count = doc.page_count
        for page_num in range(page_count):
            try:
                page     = doc.load_page(page_num)
                raw_text = page.get_text("text")
            except Exception as exc:
                logger.warning("Error on page %d of '%s': %s",
                               page_num + 1, doc_name[:60], str(exc)[:80])
                continue

            cleaned = clean_text(raw_text)
            if not cleaned or len(cleaned) < MIN_CHUNK_CHARS:
                continue

            for chunk_text in chunk_page_tokens(cleaned):
                chunk_id = f"{category.lower()}__{_safe_name(doc_name)}__{chunk_index}"
                all_chunks.append({
                    "chunk_id"    : chunk_id,
                    "doc_name"    : doc_name,
                    "doc_category": category,
                    "page_number" : page_num + 1,
                    "text"        : chunk_text,
                    "token_count" : count_tokens(chunk_text),
                    "section"     : extract_section_header(chunk_text),
                })
                chunk_index += 1

        logger.info("  [%-12s] %-65s → %3d pages, %4d chunks",
                    category, doc_name[:65], page_count, len(all_chunks))

    except Exception as exc:
        logger.error("Cannot process '%s': %s", doc_name[:70], str(exc)[:100])

    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass

    return all_chunks


def discover_pdfs(data_dir: Path) -> List[tuple]:
    """Walk data_dir and collect (pdf_path, category) pairs."""
    result = []
    for sub_dir in sorted(data_dir.iterdir()):
        if not sub_dir.is_dir():
            continue
        folder_key = sub_dir.name.lower()
        category   = CATEGORY_MAP.get(folder_key)
        if category is None:
            logger.warning("Unknown sub-folder '%s' — skipping.", sub_dir.name)
            continue
        pdfs = sorted(sub_dir.glob("*.pdf"))
        logger.info("Found %d PDFs in /%s  [%s]", len(pdfs), sub_dir.name, category)
        for pdf in pdfs:
            result.append((pdf, category))
    return result


def run_pipeline(data_dir: Path, output_path: Path) -> None:
    """Main entry point: process all PDFs → parsed_chunks.json."""
    if not data_dir.exists():
        raise FileNotFoundError(f"Data directory not found: {data_dir}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_list = discover_pdfs(data_dir)
    if not pdf_list:
        raise RuntimeError("No PDFs discovered.")

    logger.info("=" * 70)
    logger.info("Processing %d PDFs …", len(pdf_list))
    logger.info("=" * 70)

    all_chunks : List[Dict[str, Any]] = []
    failed_docs: List[str]            = []

    for pdf_path, category in pdf_list:
        chunks = extract_chunks_from_pdf(pdf_path, category)
        if not chunks and pdf_path.stat().st_size > 1000:
            failed_docs.append(pdf_path.name)
        all_chunks.extend(chunks)

    # ── Stats ─────────────────────────────────────────────────────────────────
    logger.info("=" * 70)
    logger.info("Total chunks: %d  |  Docs: %d / %d  |  Failed: %d",
                len(all_chunks), len(pdf_list) - len(failed_docs),
                len(pdf_list), len(failed_docs))

    from collections import Counter
    cats = Counter(c["doc_category"] for c in all_chunks)
    for cat, n in sorted(cats.items()):
        logger.info("  %-12s : %d chunks", cat, n)

    if failed_docs:
        logger.warning("Failed: %s", [f[:60] for f in failed_docs])

    # ── Write ──────────────────────────────────────────────────────────────────
    output = {
        "metadata": {
            "total_chunks"  : len(all_chunks),
            "total_docs"    : len(pdf_list),
            "failed_docs"   : failed_docs,
            "chunk_tokens"  : CHUNK_TOKENS,
            "overlap_tokens": OVERLAP_TOKENS,
            "categories"    : dict(cats),
        },
        "chunks": all_chunks,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    size_mb = output_path.stat().st_size / 1e6
    logger.info("Saved to %s  (%.1f MB)", output_path, size_mb)


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Legal & Tax RAG — PDF Ingestion")
    ap.add_argument("--data_dir", default="data")
    ap.add_argument("--output",   default="outputs/parsed_chunks.json")
    args = ap.parse_args()

    project_root = Path(__file__).parent.parent
    data_dir     = (project_root / args.data_dir).resolve()
    output_path  = (project_root / args.output).resolve()

    run_pipeline(data_dir, output_path)
