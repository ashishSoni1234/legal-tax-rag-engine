"""
src/graph_rag.py — Milestone 2: Citation Graph (Graph RAG Layer)
================================================================
Builds a lightweight citation graph using NetworkX:
  - Nodes: document names (Acts, Judgments, etc.)
  - Edges: Judgment → Act, when a judgment chunk mentions a specific section
           (pattern: "§ 162", "Section 164", "IRC § 6651", etc.)

Saves the graph as /outputs/citation_graph.json (edge list + node metadata).

Usage:
    python src/graph_rag.py               # build + save graph
    python src/graph_rag.py --query "§ 162"   # find related docs
"""

import re
import json
import logging
import argparse
from pathlib import Path
from typing import List, Dict, Any, Optional
from collections import defaultdict

import networkx as nx

# ─── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

CHUNKS_JSON  = "./outputs/parsed_chunks.json"
GRAPH_OUTPUT = "./outputs/citation_graph.json"

# ─── Section reference patterns ──────────────────────────────────────────────
# Match things like: §162, § 162(a), Section 162, IRC §6651, 26 U.S.C. §164
_SECTION_PAT = re.compile(
    r"""
    (?:
        (?:IRC|I\.R\.C\.|26\s+U\.S\.C?\.?)\s*    # optional IRC / USC prefix
    )?
    (?:
        §\s*\d+[\w.()]*           # § 162(a), §6651
        | section\s+\d+[\w.()]*   # section 162
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Map canonical section numbers → act document name stems (partial match)
# Will be built dynamically from acts in the chunk data.


# ─── Build graph ─────────────────────────────────────────────────────────────

def extract_cited_sections(text: str) -> List[str]:
    """Return a de-duplicated list of section references found in *text*."""
    found = _SECTION_PAT.findall(text)
    cleaned = []
    for f in found:
        # Normalise: strip extra whitespace, lower-case "section"
        f2 = re.sub(r"\s+", " ", f.strip())
        cleaned.append(f2)
    return list(dict.fromkeys(cleaned))   # preserve order, deduplicate


def _normalise_section_num(ref: str) -> Optional[str]:
    """
    Pull the bare section number from a reference string.
    '§ 162(a)' → '162',  'Section 6651' → '6651'
    Returns None if no number found.
    """
    m = re.search(r"(\d+)", ref)
    return m.group(1) if m else None


def build_citation_graph(chunks: List[Dict[str, Any]]) -> nx.DiGraph:
    """
    Scan all Judgment chunks for section citations and create edges
    Judgment_doc → Act_doc for each match.

    Also adds cross-category POV→Act / TaxDocs→Act edges when references are found.
    """
    G = nx.DiGraph()

    # Index: section_number (string) → set of act doc names
    act_sections: Dict[str, set] = defaultdict(set)
    for chunk in chunks:
        if chunk["doc_category"] == "Acts":
            sec_num = _normalise_section_num(chunk.get("section", "") or chunk["doc_name"])
            if sec_num:
                act_sections[sec_num].add(chunk["doc_name"])

    # Add all document nodes
    docs_seen: Dict[str, str] = {}   # doc_name → category
    for chunk in chunks:
        n = chunk["doc_name"]
        if n not in docs_seen:
            docs_seen[n] = chunk["doc_category"]

    for doc_name, category in docs_seen.items():
        G.add_node(doc_name, category=category)

    # Scan non-Act chunks for citations
    edge_count = 0
    for chunk in chunks:
        if chunk["doc_category"] == "Acts":
            continue   # acts don't cite themselves in this simple model

        src_doc = chunk["doc_name"]
        refs     = extract_cited_sections(chunk["text"])

        for ref in refs:
            sec_num = _normalise_section_num(ref)
            if sec_num and sec_num in act_sections:
                for act_doc in act_sections[sec_num]:
                    if not G.has_edge(src_doc, act_doc):
                        G.add_edge(src_doc, act_doc,
                                   relation="cites_section",
                                   section_refs=[])
                    G[src_doc][act_doc]["section_refs"] = list(
                        set(G[src_doc][act_doc].get("section_refs", []) + [ref])
                    )
                    edge_count += 1

    logger.info(
        "Citation graph: %d nodes, %d directed edges, %d raw citations",
        G.number_of_nodes(), G.number_of_edges(), edge_count,
    )
    return G


# ─── Persist / load ──────────────────────────────────────────────────────────

def save_graph(G: nx.DiGraph, path: str = GRAPH_OUTPUT) -> None:
    """Save graph as a JSON edge list plus node metadata."""
    data = {
        "nodes": [
            {"id": n, "category": d.get("category", "")}
            for n, d in G.nodes(data=True)
        ],
        "edges": [
            {
                "source"      : u,
                "target"      : v,
                "relation"    : d.get("relation", ""),
                "section_refs": d.get("section_refs", []),
            }
            for u, v, d in G.edges(data=True)
        ],
        "stats": {
            "num_nodes": G.number_of_nodes(),
            "num_edges": G.number_of_edges(),
        },
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.info("Citation graph saved → %s", path)


def load_graph(path: str = GRAPH_OUTPUT) -> nx.DiGraph:
    """Reconstruct a DiGraph from the JSON edge list."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    G = nx.DiGraph()
    for n in data["nodes"]:
        G.add_node(n["id"], category=n["category"])
    for e in data["edges"]:
        G.add_edge(e["source"], e["target"],
                   relation=e["relation"],
                   section_refs=e["section_refs"])
    return G


# ─── Query helpers ────────────────────────────────────────────────────────────

def get_related_acts(G: nx.DiGraph, doc_name: str) -> List[Dict[str, Any]]:
    """
    For a retrieved Judgment / POV / Tax Doc chunk, return the Act documents
    it cites, along with the specific section references.

    Returns a list of dicts: {act_doc, section_refs}
    """
    if doc_name not in G:
        return []
    related = []
    for _, target, edge_data in G.out_edges(doc_name, data=True):
        if G.nodes[target].get("category") == "Acts":
            related.append(
                {
                    "act_doc"     : target,
                    "section_refs": edge_data.get("section_refs", []),
                }
            )
    return related


def enrich_results_with_graph(
    results      : List[Dict[str, Any]],
    G            : nx.DiGraph,
    chunks_lookup: Dict[str, Dict[str, Any]],
    top_extra    : int = 2,
) -> List[Dict[str, Any]]:
    """
    For each retrieved chunk, find related Act docs via the graph and
    prepend their best chunk to the results (without duplicating).

    *chunks_lookup* is a dict mapping doc_name → list of chunks.
    """
    enriched = list(results)
    seen_docs = {r["doc_name"] for r in results}

    for result in results:
        related = get_related_acts(G, result["doc_name"])
        for rel in related[:top_extra]:
            act_name = rel["act_doc"]
            if act_name in seen_docs:
                continue
            # Take the first chunk of the act as a supplementary context
            act_chunks = chunks_lookup.get(act_name, [])
            if act_chunks:
                extra = dict(act_chunks[0])
                extra["graph_enriched"] = True
                extra["graph_reason"]   = (
                    f"Retrieved because {result['doc_name']!r} "
                    f"cites {rel['section_refs']}"
                )
                enriched.append(extra)
                seen_docs.add(act_name)

    return enriched


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Legal & Tax RAG — Citation Graph builder")
    ap.add_argument("--build", action="store_true", help="Build the citation graph.")
    ap.add_argument("--query", type=str, default="",
                    help="Show Acts cited by the document whose name contains QUERY.")
    args = ap.parse_args()

    chunks_path = Path(CHUNKS_JSON)
    if not chunks_path.exists():
        raise SystemExit(f"Run parser.py first to create {CHUNKS_JSON}")

    with open(chunks_path, encoding="utf-8") as f:
        chunks = json.load(f)["chunks"]

    if args.build or not Path(GRAPH_OUTPUT).exists():
        G = build_citation_graph(chunks)
        save_graph(G)
    else:
        G = load_graph()
        logger.info("Loaded graph: %d nodes, %d edges", G.number_of_nodes(), G.number_of_edges())

    if args.query:
        matches = [n for n in G.nodes if args.query.lower() in n.lower()]
        if not matches:
            print(f"No document found matching '{args.query}'")
        for m in matches:
            related = get_related_acts(G, m)
            print(f"\nDocument: {m}")
            if related:
                for r in related:
                    print(f"  → {r['act_doc']}  refs={r['section_refs'][:3]}")
            else:
                print("  (no outgoing Act citations)")
