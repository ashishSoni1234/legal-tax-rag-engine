"""
api/index.py — Vercel Serverless FastAPI Backend
=================================================
Vercel routes all /api/* requests here via vercel.json.
Mangum wraps the FastAPI app as an AWS Lambda / Vercel ASGI handler.

Endpoints:
    POST /api/query      — hybrid search → LLM → answer + citations
    POST /api/summarize  — doc chunks → LLM → summary
    GET  /api/health     — liveness probe
    GET  /api/documents  — list all indexed documents by category
    GET  /api/docs/{doc_name} — list chunks for a specific document

Key Vercel constraints observed:
  - Read-only filesystem: NEVER write to disk; all JSON files are pre-built.
  - 60-second max function timeout (configured in vercel.json).
  - Cold-start BM25 build runs once per warm instance, then is cached in-memory.
"""

import os
import sys
import json
import logging
from pathlib import Path
from typing import List, Dict, Any
from collections import defaultdict

from dotenv import load_dotenv

# ─── Path setup ──────────────────────────────────────────────────────────────
# When running on Vercel, __file__ is /var/task/api/index.py
# Project root is one level up from api/
_API_DIR      = Path(__file__).parent
_PROJECT_ROOT = _API_DIR.parent

# Add src/ to path so we can import search and graph_rag
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

load_dotenv(_PROJECT_ROOT / ".env", override=True)

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from openai import OpenAI
from mangum import Mangum

from search    import hybrid_search, build_index, _load_chunks
from graph_rag import load_graph, enrich_results_with_graph

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────
LLM_API_KEY  = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
LLM_MODEL    = os.getenv("LLM_MODEL",    "llama-3.3-70b-versatile")
TOP_K        = int(os.getenv("TOP_K",    "5"))

# Pre-built data files (read-only on Vercel)
CHUNKS_JSON  = str(_PROJECT_ROOT / "outputs" / "parsed_chunks.json")
GRAPH_JSON   = str(_PROJECT_ROOT / "outputs" / "citation_graph.json")

# ─── System prompt ────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a legal research assistant specializing in US tax law and legal analysis.

CRITICAL RULES — follow these exactly:
1. Answer using the provided context chunks below to the best of your ability.
2. Every factual claim in your answer must be directly supported by a specific context chunk.
3. After each claim or sentence, cite the source using this exact format:
   [Source: {doc_name}, Page {page_number}]
4. If the question cannot be fully answered from the provided context, provide whatever relevant information is available in the documents and clearly state what is not covered.
5. Do NOT use any external knowledge about US tax law, legal principles, or case outcomes beyond what appears in the context chunks.
6. Do NOT invent section numbers, case holdings, dollar amounts, dates, or any other specific facts."""


# ─── Pydantic models ──────────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    question      : str
    top_k         : int   = TOP_K
    vector_weight : float = 0.6
    keyword_weight: float = 0.4
    use_graph     : bool  = True


class QueryResponse(BaseModel):
    answer     : str
    sources    : List[Dict[str, Any]]
    chunks_used: int


class SummarizeRequest(BaseModel):
    doc_name  : str
    max_chunks: int = 10


class SummarizeResponse(BaseModel):
    doc_name   : str
    summary    : str
    chunks_used: int


# ─── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title       = "Legal & Tax RAG API",
    description = "Retrieval-Augmented Generation for US Tax & Legal documents",
    version     = "2.0.0",
    docs_url    = "/api/openapi",
    redoc_url   = None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins = ["*"],
    allow_methods = ["*"],
    allow_headers = ["*"],
)

# ─── In-process state (cached across warm invocations) ───────────────────────
_state: Dict[str, Any] = {
    "chunks"        : None,
    "chunks_lookup" : None,
    "graph"         : None,
    "client"        : None,
    "indexed"       : False,
}


def _ensure_initialized() -> None:
    """Load chunks + graph + Pinecone index on first request (warm-start cache)."""
    if _state["indexed"]:
        return

    # ── Load pre-built chunks (read-only) ─────────────────────────────────────
    chunks_path = Path(CHUNKS_JSON)
    if not chunks_path.exists():
        raise HTTPException(
            status_code=503,
            detail=f"Chunk data not found at {CHUNKS_JSON}. Run: python src/parser.py locally.",
        )

    logger.info("Loading chunks from %s …", CHUNKS_JSON)
    with open(chunks_path, encoding="utf-8") as f:
        data = json.load(f)
    chunks = data["chunks"]
    _state["chunks"] = chunks

    # Build lookup: doc_name → list[chunk]
    lookup: Dict[str, list] = defaultdict(list)
    for c in chunks:
        lookup[c["doc_name"]].append(c)
    _state["chunks_lookup"] = dict(lookup)

    # ── Load pre-built citation graph (read-only — NEVER rebuild on Vercel) ───
    graph_path = Path(GRAPH_JSON)
    if graph_path.exists():
        try:
            _state["graph"] = load_graph(GRAPH_JSON)
            logger.info(
                "Citation graph loaded: %d nodes, %d edges",
                _state["graph"].number_of_nodes(),
                _state["graph"].number_of_edges(),
            )
        except Exception as e:
            logger.warning("Could not load graph: %s — graph enrichment disabled.", e)
            _state["graph"] = None
    else:
        logger.warning(
            "citation_graph.json not found at %s — graph enrichment disabled. "
            "Run: python src/graph_rag.py --build  locally to generate it.",
            GRAPH_JSON,
        )
        _state["graph"] = None

    # ── Connect to Pinecone (Gemini embeddings already indexed) ───────────────
    logger.info("Connecting to Pinecone index …")
    build_index()   # no-op if vectors already present; never writes to disk

    # ── LLM client ────────────────────────────────────────────────────────────
    _state["client"] = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)

    _state["indexed"] = True
    logger.info("Initialization complete. %d chunks ready.", len(chunks))


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _build_context_block(results: List[Dict[str, Any]]) -> str:
    """Format retrieved chunks as a numbered context block for the LLM."""
    lines = []
    for i, r in enumerate(results, 1):
        okf      = r.get("okf_context", {})
        source   = okf.get("source",   r.get("doc_name",    "Unknown"))
        page     = okf.get("page",     r.get("page_number", "?"))
        section  = okf.get("section",  r.get("section",     ""))
        category = okf.get("category", r.get("doc_category",""))
        content  = okf.get("content",  r.get("text",        ""))

        header = f"[CONTEXT {i}] Source: {source}, Page: {page}"
        if section:
            header += f", Section: {section}"
        if category:
            header += f", Category: {category}"
        lines.append(header)
        lines.append(content)
        lines.append("")

    return "\n".join(lines)


def _call_llm(system: str, user_message: str) -> str:
    """Call LLM API via OpenAI-compatible client and return the text response."""
    client = _state.get("client")
    if not client:
        raise HTTPException(status_code=503, detail="LLM client not configured.")

    response = client.chat.completions.create(
        model       = LLM_MODEL,
        temperature = 0,
        messages    = [
            {"role": "system", "content": system},
            {"role": "user",   "content": user_message},
        ],
    )
    return response.choices[0].message.content


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {
        "status" : "ok",
        "indexed": _state["indexed"],
        "model"  : LLM_MODEL,
        "graph"  : _state["graph"] is not None,
    }


@app.post("/api/query", response_model=QueryResponse)
def query(req: QueryRequest):
    """
    Main Q&A endpoint.

    1. Hybrid search (Pinecone vector + BM25 keyword) for top-k chunks.
    2. Optionally enrich with Graph RAG (pulls related Acts via citation graph).
    3. Send structured OKF context to Groq LLM.
    4. Return answer + source citations.
    """
    _ensure_initialized()

    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    # Step 1: Retrieve — pass pre-loaded chunks to BM25 (avoids re-loading JSON)
    results = hybrid_search(
        req.question,
        top_k          = req.top_k,
        vector_weight  = req.vector_weight,
        keyword_weight = req.keyword_weight,
        chunks         = _state["chunks"],
    )

    if not results:
        raise HTTPException(status_code=404, detail="No relevant documents found.")

    # Step 2: Graph enrichment (read-only graph — pre-built)
    if req.use_graph and _state.get("graph") is not None:
        results = enrich_results_with_graph(
            results,
            _state["graph"],
            _state["chunks_lookup"],
        )

    # Step 3: Build context and call LLM
    context_block = _build_context_block(results)
    user_msg = f"""Here are the retrieved context chunks:

{context_block}

---
Question: {req.question}

Please answer the question using ONLY the context chunks above, citing each source."""

    answer = _call_llm(SYSTEM_PROMPT, user_msg)

    # Step 4: Build source list
    sources = [
        {
            "doc_name"      : r.get("doc_name",    r.get("okf_context", {}).get("source", "")),
            "page_number"   : r.get("page_number", r.get("okf_context", {}).get("page", "")),
            "category"      : r.get("doc_category",r.get("okf_context", {}).get("category","")),
            "section"       : r.get("section",     r.get("okf_context", {}).get("section","")),
            "hybrid_score"  : round(r.get("hybrid_score", 0.0), 4),
            "graph_enriched": r.get("graph_enriched", False),
        }
        for r in results
    ]

    return QueryResponse(answer=answer, sources=sources, chunks_used=len(results))


@app.post("/api/summarize", response_model=SummarizeResponse)
def summarize(req: SummarizeRequest):
    """Summarize a document by retrieving its chunks and passing to LLM."""
    _ensure_initialized()

    doc_chunks = _state["chunks_lookup"].get(req.doc_name)
    if not doc_chunks:
        matches = [d for d in _state["chunks_lookup"] if req.doc_name.lower() in d.lower()]
        if not matches:
            raise HTTPException(
                status_code=404, detail=f"Document '{req.doc_name}' not found."
            )
        doc_chunks = _state["chunks_lookup"][matches[0]]
        doc_name   = matches[0]
    else:
        doc_name = req.doc_name

    # Distribute sample: first + middle + last
    n = len(doc_chunks)
    if n <= req.max_chunks:
        sample = doc_chunks[:]
    else:
        third  = req.max_chunks // 3
        first  = doc_chunks[:third]
        mid_s  = (n // 2) - (third // 2)
        middle = doc_chunks[mid_s : mid_s + third]
        last   = doc_chunks[-(req.max_chunks - 2 * third):]
        sample = first + middle + last

    context_block = _build_context_block([
        {**c, "okf_context": {
            "source": c["doc_name"], "category": c["doc_category"],
            "page": c["page_number"], "section": c.get("section", ""),
            "content": c["text"],
        }}
        for c in sample
    ])

    system = (
        "You are a legal research assistant. "
        "Summarize the provided document excerpts concisely and accurately. "
        "Only include information explicitly present in the context. "
        "Structure: 1) Document type & subject, 2) Key provisions/findings, "
        "3) Important numbers/thresholds/dates if any."
    )
    user_msg = f"""Please summarize the following document excerpts:

{context_block}

Provide a concise, structured summary."""

    summary = _call_llm(system, user_msg)

    return SummarizeResponse(doc_name=doc_name, summary=summary, chunks_used=len(sample))


@app.get("/api/docs/{doc_name}")
def list_doc_chunks(doc_name: str):
    """List available chunks for a specific document."""
    _ensure_initialized()
    matches = [d for d in _state["chunks_lookup"] if doc_name.lower() in d.lower()]
    result  = {}
    for m in matches[:5]:
        result[m] = [
            {"chunk_id": c["chunk_id"], "page": c["page_number"], "tokens": c["token_count"]}
            for c in _state["chunks_lookup"][m]
        ]
    return result


@app.get("/api/documents")
def list_documents():
    """List all indexed documents grouped by category."""
    _ensure_initialized()
    by_cat: Dict[str, list] = defaultdict(list)
    for c in _state["chunks"]:
        n   = c["doc_name"]
        cat = c["doc_category"]
        if n not in by_cat[cat]:
            by_cat[cat].append(n)
    return {cat: sorted(set(docs)) for cat, docs in by_cat.items()}


# ─── Vercel ASGI handler ──────────────────────────────────────────────────────
handler = Mangum(app, lifespan="off")
