"""
src/search.py — Milestone 2: Hybrid Search (Vector + Keyword)
=============================================================
Stack:
  - Embeddings  : Ollama nomic-embed-text (768-dim, local)
  - Vector store: Pinecone (cloud, cosine similarity)
  - Keyword     : Elasticsearch BM25 (optional, auto-detected)
  - Hybrid score: 0.6 × vector + 0.4 × keyword (configurable)

Usage (standalone):
    python src/search.py --build
    python src/search.py --query "What is gross income under §61?"
"""

import os
import json
import logging
import argparse
import urllib.request
from pathlib import Path
from typing import List, Dict, Any, Optional

from dotenv import load_dotenv

# ─── Env ─────────────────────────────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(_PROJECT_ROOT / ".env", override=True)

PINECONE_API_KEY    = os.getenv("PINECONE_API_KEY", "")
PINECONE_ENV        = os.getenv("PINECONE_ENVIRONMENT", "us-east-1")
PINECONE_INDEX_NAME = "legal-tax-rag"

OLLAMA_BASE_URL     = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
EMBED_MODEL         = os.getenv("EMBED_MODEL", "nomic-embed-text")
EMBED_DIM           = 768   # nomic-embed-text output dimension

ES_HOST             = os.getenv("ES_HOST",   "http://localhost:9200")
ES_INDEX            = os.getenv("ES_INDEX",  "legal_tax_rag")
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
_pinecone_index = None
_es_client      = None
_es_available   = None   # None = not yet probed


# ─── Ollama Embeddings ───────────────────────────────────────────────────────

def _embed_texts(texts: List[str]) -> List[List[float]]:
    """
    Call Ollama REST API to embed a list of texts.
    Raises RuntimeError if Ollama is unreachable — no silent fallback.
    """
    url  = f"{OLLAMA_BASE_URL}/api/embeddings"
    embeddings: List[List[float]] = []

    for text in texts:
        payload = json.dumps({
            "model"  : EMBED_MODEL,
            "prompt" : text
        }).encode("utf-8")
        req = urllib.request.Request(
            url,
            data    = payload,
            headers = {"Content-Type": "application/json"},
            method  = "POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode())
                emb    = result.get("embedding")
                if not emb or len(emb) != EMBED_DIM:
                    raise ValueError(
                        f"Ollama returned embedding of length {len(emb) if emb else 0}, "
                        f"expected {EMBED_DIM}. Is '{EMBED_MODEL}' pulled?"
                    )
                embeddings.append(emb)
        except (urllib.error.URLError, ConnectionRefusedError) as exc:
            raise RuntimeError(
                f"Cannot reach Ollama at {OLLAMA_BASE_URL}. "
                f"Run: ollama serve  (and: ollama pull {EMBED_MODEL}). Error: {exc}"
            ) from exc

    return embeddings


def embed_query(query: str) -> List[float]:
    """Embed a single query string. Raises RuntimeError if Ollama is down."""
    return _embed_texts([query])[0]


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
            name   = PINECONE_INDEX_NAME,
            dimension = EMBED_DIM,
            metric = "cosine",
            spec   = ServerlessSpec(cloud="aws", region=PINECONE_ENV),
        )
        logger.info("Pinecone index created.")

    _pinecone_index = pc.Index(PINECONE_INDEX_NAME)
    logger.info("Connected to Pinecone index: %s", PINECONE_INDEX_NAME)
    return _pinecone_index


# ─── Elasticsearch ───────────────────────────────────────────────────────────

def _get_es() -> Optional[Any]:
    """Return Elasticsearch client if running, else None. Cached."""
    global _es_client, _es_available
    if _es_available is not None:
        return _es_client if _es_available else None

    # Fast TCP probe first (avoids long timeout)
    import socket
    from urllib.parse import urlparse
    parsed = urlparse(ES_HOST)
    host   = parsed.hostname or "localhost"
    port   = parsed.port    or 9200
    try:
        s = socket.create_connection((host, port), timeout=2.0)
        s.close()
    except (socket.timeout, ConnectionRefusedError, OSError):
        logger.warning(
            "Elasticsearch not running at %s — keyword search disabled. "
            "Start it with: docker-compose up -d", ES_HOST
        )
        _es_available = False
        return None

    # Port open → try real client
    try:
        from elasticsearch import Elasticsearch
        client = Elasticsearch(ES_HOST, request_timeout=5)
        if client.ping():
            _es_client    = client
            _es_available = True
            logger.info("Elasticsearch connected at %s", ES_HOST)
        else:
            raise ConnectionError("Ping failed")
    except Exception as exc:
        logger.warning("Elasticsearch unavailable (%s) — falling back to vector-only.", exc)
        _es_available = False

    return _es_client if _es_available else None


def _es_setup_index(es) -> None:
    """Create ES index with BM25 English-analyzer mapping if absent."""
    if es.indices.exists(index=ES_INDEX):
        return
    es.indices.create(
        index = ES_INDEX,
        body  = {
            "settings": {
                "number_of_shards"  : 1,
                "number_of_replicas": 0,
            },
            "mappings": {
                "properties": {
                    "text"        : {"type": "text",    "analyzer": "english"},
                    "doc_name"    : {"type": "keyword"},
                    "doc_category": {"type": "keyword"},
                    "page_number" : {"type": "integer"},
                    "section"     : {"type": "keyword"},
                }
            },
        },
    )
    logger.info("Created Elasticsearch index: %s", ES_INDEX)


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
    Embed all chunks and upsert into Pinecone.
    If Elasticsearch is available, also index for BM25.
    """
    idx = _get_pinecone_index()
    es  = _get_es()

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

    # ── Pinecone ─────────────────────────────────────────────────────────────
    batch_size = 32
    logger.info("Embedding %d chunks via Ollama + upserting to Pinecone …", len(chunks))
    for start in range(0, len(chunks), batch_size):
        end          = min(start + batch_size, len(chunks))
        batch_texts  = texts [start:end]
        batch_ids    = ids   [start:end]
        batch_metas  = metas [start:end]

        embeddings = _embed_texts(batch_texts)   # raises if Ollama down

        vectors = [
            {"id": bid, "values": emb, "metadata": meta}
            for bid, emb, meta in zip(batch_ids, embeddings, batch_metas)
        ]
        idx.upsert(vectors=vectors)
        logger.info("  Pinecone: upserted chunks %d–%d", start, end - 1)

    logger.info("Pinecone upsert complete (%d chunks).", len(chunks))

    # ── Elasticsearch ─────────────────────────────────────────────────────────
    if es:
        _es_setup_index(es)
        logger.info("Indexing %d chunks into Elasticsearch …", len(chunks))
        from elasticsearch.helpers import bulk
        actions = [
            {
                "_index" : ES_INDEX,
                "_id"    : c["chunk_id"],
                "_source": {
                    "text"        : c["text"],
                    "doc_name"    : c["doc_name"],
                    "doc_category": c["doc_category"],
                    "page_number" : c["page_number"],
                    "section"     : c.get("section", ""),
                },
            }
            for c in chunks
        ]
        success, errors = bulk(es, actions, request_timeout=60)
        logger.info(
            "Elasticsearch: indexed %d docs (%d errors).", success, len(errors)
        )


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


def _keyword_search(query: str, top_k: int) -> List[Dict[str, Any]]:
    """BM25 keyword search via Elasticsearch. Returns [] if ES unavailable."""
    es = _get_es()
    if not es:
        return []

    resp = es.search(
        index = ES_INDEX,
        body  = {
            "query": {
                "multi_match": {
                    "query"   : query,
                    "fields"  : ["text^3", "section^2", "doc_name"],
                    "operator": "or",
                    "type"    : "best_fields",
                }
            },
            "size": top_k * 2,
        },
    )
    max_score = resp["hits"]["max_score"] or 1.0
    hits = []
    for hit in resp["hits"]["hits"]:
        src = hit["_source"]
        hits.append({
            "chunk_id"     : hit["_id"],
            "text"         : src.get("text",         ""),
            "doc_name"     : src.get("doc_name",     ""),
            "doc_category" : src.get("doc_category", ""),
            "page_number"  : src.get("page_number",  0),
            "section"      : src.get("section",      ""),
            "vector_score" : 0.0,
            "keyword_score": (hit["_score"] / max_score) if max_score > 0 else 0.0,
        })
    return hits


def hybrid_search(
    query          : str,
    top_k          : int   = 5,
    vector_weight  : float = VECTOR_WEIGHT,
    keyword_weight : float = KEYWORD_WEIGHT,
) -> List[Dict[str, Any]]:
    """
    Hybrid search: Pinecone vector + Elasticsearch BM25.

    If Elasticsearch is unavailable, vector_weight is forced to 1.0.

    Returns up to *top_k* chunk dicts, each with:
        chunk_id, doc_name, doc_category, page_number, section,
        text, vector_score, keyword_score, hybrid_score, okf_context
    """
    es_ok = _get_es() is not None
    if not es_ok:
        vector_weight  = 1.0
        keyword_weight = 0.0

    vec_hits = _vector_search(query, top_k)
    kw_hits  = _keyword_search(query, top_k) if es_ok else []

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
    Ensure Pinecone index is populated.
    Skips upsert if index already contains vectors.
    Also indexes into Elasticsearch if available.
    """
    idx   = _get_pinecone_index()
    stats = idx.describe_index_stats()
    count = stats.get("total_vector_count", 0)

    if count > 0:
        logger.info(
            "Pinecone index already contains %d vectors. Skipping upsert.", count
        )
        # Still try to populate ES if it just came online
        es = _get_es()
        if es:
            try:
                es_count = es.count(index=ES_INDEX).get("count", 0)
                if es_count == 0:
                    chunks = _load_chunks()
                    _es_setup_index(es)
                    from elasticsearch.helpers import bulk
                    actions = [
                        {
                            "_index" : ES_INDEX,
                            "_id"    : c["chunk_id"],
                            "_source": {
                                "text"        : c["text"],
                                "doc_name"    : c["doc_name"],
                                "doc_category": c["doc_category"],
                                "page_number" : c["page_number"],
                                "section"     : c.get("section", ""),
                            },
                        }
                        for c in chunks
                    ]
                    success, _ = bulk(es, actions, request_timeout=60)
                    logger.info("ES: indexed %d docs.", success)
            except Exception as exc:
                logger.warning("ES indexing skipped: %s", exc)
    else:
        chunks = _load_chunks()
        logger.info("Loaded %d chunks from %s", len(chunks), CHUNKS_JSON)
        index_chunks(chunks)

    logger.info("Search index ready.")


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Legal & Tax RAG — Search layer")
    ap.add_argument("--build",  action="store_true", help="Build/refresh the index")
    ap.add_argument("--query",  type=str, default="", help="Run a test query")
    ap.add_argument("--top_k", type=int,  default=5)
    args = ap.parse_args()

    if args.build:
        build_index()

    if args.query:
        results = hybrid_search(args.query, top_k=args.top_k)
        es_on   = _get_es() is not None
        mode    = f"hybrid ({VECTOR_WEIGHT}v+{KEYWORD_WEIGHT}k)" if es_on else "vector-only"
        print(f"\n=== Top {len(results)} [{mode}] for: '{args.query}' ===\n")
        for i, r in enumerate(results, 1):
            print(
                f"[{i}] {r['doc_name'][:70]}  p.{r['page_number']}"
                f"  hybrid={r['hybrid_score']:.3f}"
                f"  vec={r['vector_score']:.3f}"
                f"  kw={r['keyword_score']:.3f}"
            )
            print(f"     {r['text'][:160].strip()} …\n")
