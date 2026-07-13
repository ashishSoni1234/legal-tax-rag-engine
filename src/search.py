"""
src/search.py — Vercel Edition: Hybrid Search (Vector + BM25)
=============================================================
Stack:
  - Embeddings  : Google Gemini API (models/text-embedding-004, 768-dim)
  - Vector store: Pinecone (cloud, cosine similarity)
  - Keyword     : rank_bm25 (in-memory BM25Okapi — no Docker required)
  - Hybrid score: 0.6 × vector + 0.4 × keyword (configurable)

Usage (standalone):
    python src/search.py --build
    python src/search.py --query "What is gross income under §61?"
"""

import os
import re
import json
import logging
import argparse
from pathlib import Path
from typing import List, Dict, Any, Optional

from dotenv import load_dotenv

# ─── Env ─────────────────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(_PROJECT_ROOT / ".env", override=True)

PINECONE_API_KEY    = os.getenv("PINECONE_API_KEY", "")
PINECONE_ENV        = os.getenv("PINECONE_ENVIRONMENT", "us-east-1")
PINECONE_INDEX_NAME = "legal-tax-rag"

GEMINI_API_KEY      = os.getenv("GEMINI_API_KEY", "")
GEMINI_EMBED_MODEL  = "gemini-embedding-001"
EMBED_DIM           = 768   # text-embedding-004 output dimension

VECTOR_WEIGHT       = float(os.getenv("VECTOR_WEIGHT",  "0.6"))
KEYWORD_WEIGHT      = float(os.getenv("KEYWORD_WEIGHT", "0.4"))

CHUNKS_JSON         = str(_PROJECT_ROOT / "outputs" / "parsed_chunks.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ─── Lazy singletons ─────────────────────────────────────────────────────────
_pinecone_index  = None
_bm25_index      = None   # BM25Okapi instance
_bm25_chunks     = None   # parallel list of chunk dicts matching BM25 corpus


# ─── Google Gemini Embeddings (google.genai SDK) ─────────────────────────────

def _get_gemini_client():
    """Return a configured google.genai Client instance."""
    if not GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY is not set in .env. "
            "Get a key at https://aistudio.google.com/app/apikey"
        )
    from google import genai
    return genai.Client(api_key=GEMINI_API_KEY)


def _embed_texts(texts: List[str]) -> List[List[float]]:
    """
    Call Google Gemini API to embed a list of texts using google.genai SDK.
    Processes texts individually to avoid batch API complexity.
    Raises RuntimeError if API key is missing or API call fails.
    """
    client = _get_gemini_client()
    embeddings: List[List[float]] = []

    for text in texts:
        try:
            result = client.models.embed_content(
                model   = GEMINI_EMBED_MODEL,
                contents= text,
                config  = {"task_type": "RETRIEVAL_DOCUMENT", "output_dimensionality": EMBED_DIM},
            )
            emb = result.embeddings[0].values
            embeddings.append(list(emb))
        except Exception as exc:
            raise RuntimeError(
                f"Gemini embedding API call failed: {exc}"
            ) from exc

    return embeddings


def embed_query(query: str) -> List[float]:
    """Embed a single query string using Gemini."""
    client = _get_gemini_client()
    try:
        result = client.models.embed_content(
            model   = GEMINI_EMBED_MODEL,
            contents= query,
            config  = {"task_type": "RETRIEVAL_QUERY", "output_dimensionality": EMBED_DIM},
        )
        return list(result.embeddings[0].values)
    except Exception as exc:
        raise RuntimeError(f"Gemini query embedding failed: {exc}") from exc


# ─── Pinecone ────────────────────────────────────────────────────────────────

def _get_pinecone_index():
    """Return (and lazily initialise) the Pinecone Index object."""
    global _pinecone_index
    if _pinecone_index is not None:
        return _pinecone_index

    if not PINECONE_API_KEY:
        raise ValueError(
            "PINECONE_API_KEY is not set in .env. "
            "Get a free key at https://www.pinecone.io/"
        )

    from pinecone import Pinecone, ServerlessSpec

    pc = Pinecone(api_key=PINECONE_API_KEY)

    existing = [idx.name for idx in pc.list_indexes()]
    if PINECONE_INDEX_NAME not in existing:
        logger.info("Creating Pinecone index '%s' …", PINECONE_INDEX_NAME)
        pc.create_index(
            name      = PINECONE_INDEX_NAME,
            dimension = EMBED_DIM,
            metric    = "cosine",
            spec      = ServerlessSpec(cloud="aws", region=PINECONE_ENV),
        )
        logger.info("Pinecone index created.")

    _pinecone_index = pc.Index(PINECONE_INDEX_NAME)
    logger.info("Connected to Pinecone index: %s", PINECONE_INDEX_NAME)
    return _pinecone_index


# ─── BM25 (rank_bm25 — in-memory) ────────────────────────────────────────────

def _tokenize(text: str) -> List[str]:
    """
    Simple whitespace + punctuation tokenizer optimized for legal text.
    Lowercases, strips punctuation, removes stopwords and short tokens.
    Designed to be fast for Vercel cold-start constraints.
    """
    _STOPWORDS = {
        "a", "an", "the", "and", "or", "in", "on", "at", "to", "for",
        "of", "with", "is", "are", "was", "were", "be", "been", "being",
        "that", "this", "it", "its", "by", "as", "from", "not", "no",
    }
    text = text.lower()
    tokens = re.findall(r"[a-z0-9§]+(?:[.\-'][a-z0-9]+)*", text)
    return [t for t in tokens if len(t) > 1 and t not in _STOPWORDS]


def _get_bm25_index(chunks: Optional[List[Dict[str, Any]]] = None):
    """
    Build (and cache) an in-memory BM25Okapi index.
    If *chunks* is None, loads from CHUNKS_JSON automatically.
    """
    global _bm25_index, _bm25_chunks
    if _bm25_index is not None:
        return _bm25_index, _bm25_chunks

    from rank_bm25 import BM25Okapi

    if chunks is None:
        chunks = _load_chunks()

    logger.info("Building in-memory BM25 index for %d chunks …", len(chunks))
    corpus = [_tokenize(c["text"]) for c in chunks]
    _bm25_index  = BM25Okapi(corpus)
    _bm25_chunks = chunks
    logger.info("BM25 index ready.")
    return _bm25_index, _bm25_chunks


# ─── OKF wrapper ─────────────────────────────────────────────────────────────

def wrap_okf(chunk: Dict[str, Any]) -> Dict[str, Any]:
    """
    Wrap a raw chunk dict in a structured OKF-style schema for LLM context:
        {source, category, page, section, content}
    """
    return {
        "source"  : chunk.get("doc_name",    ""),
        "category": chunk.get("doc_category",""),
        "page"    : chunk.get("page_number", 0),
        "section" : chunk.get("section",     ""),
        "content" : chunk.get("text",        ""),
    }


# ─── Indexing ─────────────────────────────────────────────────────────────────

def index_chunks(chunks: List[Dict[str, Any]]) -> None:
    """
    Embed all chunks via Gemini and upsert into Pinecone.
    BM25 index is built in-memory on demand — no separate indexing step needed.
    """
    idx = _get_pinecone_index()

    texts  = [c["text"]      for c in chunks]
    ids    = [c["chunk_id"]  for c in chunks]
    metas  = [
        {
            "doc_name"    : c["doc_name"],
            "doc_category": c["doc_category"],
            "page_number" : int(c["page_number"]),
            "section"     : c.get("section", ""),
            "text"        : c["text"][:10_000],  # Pinecone metadata limit
        }
        for c in chunks
    ]

    # ── Pinecone (Gemini embeddings) ──────────────────────────────────────────
    batch_size = 32
    logger.info(
        "Embedding %d chunks via Gemini + upserting to Pinecone …", len(chunks)
    )
    for start in range(0, len(chunks), batch_size):
        end         = min(start + batch_size, len(chunks))
        batch_texts = texts [start:end]
        batch_ids   = ids   [start:end]
        batch_metas = metas [start:end]

        embeddings = _embed_texts(batch_texts)

        vectors = [
            {"id": bid, "values": emb, "metadata": meta}
            for bid, emb, meta in zip(batch_ids, embeddings, batch_metas)
        ]
        idx.upsert(vectors=vectors)
        logger.info("  Pinecone: upserted chunks %d–%d", start, end - 1)

    logger.info("Pinecone upsert complete (%d chunks).", len(chunks))


# ─── Search ──────────────────────────────────────────────────────────────────

def _vector_search(query: str, top_k: int) -> List[Dict[str, Any]]:
    """Pinecone ANN vector search. Returns up to top_k*2 candidates."""
    idx       = _get_pinecone_index()
    query_vec = embed_query(query)

    res  = idx.query(vector=query_vec, top_k=top_k * 2, include_metadata=True)
    hits = []
    for match in res.get("matches", []):
        meta = match.metadata or {}
        hits.append({
            "chunk_id"     : match.id,
            "text"         : meta.get("text",         ""),
            "doc_name"     : meta.get("doc_name",     ""),
            "doc_category" : meta.get("doc_category", ""),
            "page_number"  : meta.get("page_number",  0),
            "section"      : meta.get("section",      ""),
            "vector_score" : float(match.score),
            "keyword_score": 0.0,
        })
    return hits


def _keyword_search(
    query: str,
    top_k: int,
    chunks: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """BM25 keyword search using in-memory rank_bm25 index."""
    bm25, bm25_chunks = _get_bm25_index(chunks)
    query_tokens = _tokenize(query)

    if not query_tokens:
        return []

    scores = bm25.get_scores(query_tokens)
    max_score = float(scores.max()) if scores.max() > 0 else 1.0

    # Get top candidates
    top_indices = scores.argsort()[::-1][: top_k * 2]

    hits = []
    for idx in top_indices:
        score = float(scores[idx])
        if score <= 0:
            break
        c = bm25_chunks[idx]
        hits.append({
            "chunk_id"     : c["chunk_id"],
            "text"         : c["text"],
            "doc_name"     : c["doc_name"],
            "doc_category" : c["doc_category"],
            "page_number"  : c["page_number"],
            "section"      : c.get("section", ""),
            "vector_score" : 0.0,
            "keyword_score": score / max_score,
        })
    return hits


def hybrid_search(
    query          : str,
    top_k          : int   = 5,
    vector_weight  : float = VECTOR_WEIGHT,
    keyword_weight : float = KEYWORD_WEIGHT,
    chunks         : Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Hybrid search: Pinecone vector + in-memory BM25 (rank_bm25).

    Returns up to *top_k* chunk dicts, each with:
        chunk_id, doc_name, doc_category, page_number, section,
        text, vector_score, keyword_score, hybrid_score, okf_context
    """
    vec_hits = _vector_search(query, top_k)
    kw_hits  = _keyword_search(query, top_k, chunks)

    # Merge by chunk_id
    merged: Dict[str, Dict] = {}
    for h in vec_hits:
        merged[h["chunk_id"]] = dict(h)
    for h in kw_hits:
        cid = h["chunk_id"]
        if cid in merged:
            merged[cid]["keyword_score"] = h["keyword_score"]
        else:
            merged[cid] = dict(h)

    # Hybrid score
    for entry in merged.values():
        entry["hybrid_score"] = (
            vector_weight  * entry.get("vector_score",  0.0) +
            keyword_weight * entry.get("keyword_score", 0.0)
        )

    ranked = sorted(
        merged.values(),
        key     = lambda x: x["hybrid_score"],
        reverse = True,
    )[:top_k]

    # Attach OKF wrapper
    for entry in ranked:
        entry["okf_context"] = wrap_okf(entry)

    return ranked


# ─── Index management ────────────────────────────────────────────────────────

def _load_chunks() -> List[Dict[str, Any]]:
    p = Path(CHUNKS_JSON)
    if not p.exists():
        raise FileNotFoundError(
            f"{CHUNKS_JSON} not found. Run parser first:\n"
            "  python src/parser.py"
        )
    with open(p, encoding="utf-8") as f:
        return json.load(f)["chunks"]


def build_index() -> None:
    """
    Ensure Pinecone index is populated with Gemini embeddings.
    BM25 index is built in-memory automatically on first search call.
    Skips Pinecone upsert if index already contains vectors.
    """
    idx   = _get_pinecone_index()
    stats = idx.describe_index_stats()
    count = stats.get("total_vector_count", 0)

    if count > 0:
        logger.info(
            "Pinecone index already contains %d vectors. Skipping upsert.", count
        )
    else:
        chunks = _load_chunks()
        logger.info("Loaded %d chunks from %s", len(chunks), CHUNKS_JSON)
        index_chunks(chunks)

    logger.info("Search index ready.")


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Legal & Tax RAG — Search layer (Vercel Edition)")
    ap.add_argument("--build",  action="store_true", help="Build/refresh the Pinecone index")
    ap.add_argument("--query",  type=str, default="", help="Run a test hybrid search query")
    ap.add_argument("--top_k", type=int,  default=5)
    args = ap.parse_args()

    if args.build:
        build_index()

    if args.query:
        results = hybrid_search(args.query, top_k=args.top_k)
        print(f"\n=== Top {len(results)} [hybrid {VECTOR_WEIGHT}v+{KEYWORD_WEIGHT}k] for: '{args.query}' ===\n")
        for i, r in enumerate(results, 1):
            print(
                f"[{i}] {r['doc_name'][:70]}  p.{r['page_number']}"
                f"  hybrid={r['hybrid_score']:.3f}"
                f"  vec={r['vector_score']:.3f}"
                f"  kw={r['keyword_score']:.3f}"
            )
            print(f"     {r['text'][:160].strip()} …\n")
