"""
src/api.py — Milestone 3: FastAPI Backend
==========================================
Endpoints:
    POST /query      — hybrid search → Claude LLM → answer + citations
    POST /summarize  — all chunks for a doc → Claude → concise summary
    GET  /health     — liveness probe
    GET  /docs/{doc_name} — list chunks for a document

Run:
    uvicorn src.api:app --reload --port 8000
"""

import os
import sys
import io
import json
import logging
from pathlib import Path
from typing import List, Optional, Dict, Any

# ─── UTF-8 fix ───────────────────────────────────────────────────────────────

if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv(override=True)

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from openai import OpenAI

# ─── Local imports ────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
from search    import hybrid_search, build_index, _load_chunks
from graph_rag import load_graph, enrich_results_with_graph, build_citation_graph, save_graph

logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────
LLM_API_KEY       = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL      = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
LLM_MODEL         = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
TOP_K             = int(os.getenv("TOP_K", "5"))
CHUNKS_JSON       = "./outputs/parsed_chunks.json"
GRAPH_JSON        = "./outputs/citation_graph.json"

# ─── System prompt (hallucination-prevention) ─────────────────────────────────
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
    answer  : str
    sources : List[Dict[str, Any]]
    chunks_used: int


class SummarizeRequest(BaseModel):
    doc_name: str
    max_chunks: int = 10


class SummarizeResponse(BaseModel):
    doc_name: str
    summary : str
    chunks_used: int


# ─── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Legal & Tax RAG API",
    description="Retrieval-Augmented Generation for US Tax & Legal documents",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Lazy-loaded state ────────────────────────────────────────────────────────
_state: Dict[str, Any] = {
    "chunks"        : None,   # list of all chunk dicts
    "chunks_lookup" : None,   # doc_name → list[chunk]
    "graph"         : None,   # NetworkX DiGraph
    "client"        : None,   # OpenAI client
    "indexed"       : False,
}


def _ensure_initialized() -> None:
    """Load chunks + graph + search index on first request."""
    if _state["indexed"]:
        return

    chunks_path = Path(CHUNKS_JSON)
    if not chunks_path.exists():
        raise HTTPException(
            status_code=503,
            detail=f"Chunk data not found at {CHUNKS_JSON}. Run: python src/parser.py"
        )

    logger.info("Loading chunks from %s …", CHUNKS_JSON)
    with open(chunks_path, encoding="utf-8") as f:
        data = json.load(f)
    chunks = data["chunks"]
    _state["chunks"] = chunks

    # Build lookup: doc_name → list[chunk]
    from collections import defaultdict
    lookup: Dict[str, list] = defaultdict(list)
    for c in chunks:
        lookup[c["doc_name"]].append(c)
    _state["chunks_lookup"] = dict(lookup)

    # Build / load graph
    graph_path = Path(GRAPH_JSON)
    if graph_path.exists():
        try:
            _state["graph"] = load_graph(GRAPH_JSON)
            logger.info("Citation graph loaded: %d nodes, %d edges",
                        _state["graph"].number_of_nodes(),
                        _state["graph"].number_of_edges())
        except Exception as e:
            logger.warning("Could not load graph: %s — rebuilding.", e)
            _state["graph"] = _rebuild_graph(chunks)
    else:
        _state["graph"] = _rebuild_graph(chunks)

    # Build search index
    logger.info("Building search index …")
    build_index()

    # OpenAI client for Llama 3.1
    _state["client"] = OpenAI(api_key=LLM_API_KEY, base_url=LLM_BASE_URL)

    _state["indexed"] = True
    logger.info("Initialization complete. %d chunks ready.", len(chunks))


def _rebuild_graph(chunks):
    from graph_rag import build_citation_graph, save_graph
    G = build_citation_graph(chunks)
    save_graph(G)
    return G


def _build_context_block(results: List[Dict[str, Any]]) -> str:
    """Format retrieved chunks as a numbered context block for the LLM."""
    lines = []
    for i, r in enumerate(results, 1):
        okf = r.get("okf_context", {})
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
    """Call Llama 3.1 API via OpenAI client and return the text response."""
    client = _state.get("client")
    if not client:
        raise HTTPException(status_code=503, detail="LLM client not configured.")

    response = client.chat.completions.create(
        model=LLM_MODEL,
        temperature=0,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_message}
        ],
    )
    return response.choices[0].message.content


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {
        "status" : "ok",
        "indexed": _state["indexed"],
        "model"  : LLM_MODEL,
    }


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest):
    """
    Main Q&A endpoint.

    1. Hybrid search (vector + keyword) for top-k chunks.
    2. Optionally enrich with Graph RAG (pull related Acts).
    3. Send structured OKF context to Claude.
    4. Return answer + source citations.
    """
    _ensure_initialized()

    if not req.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    # Step 1: Retrieve
    results = hybrid_search(
        req.question,
        top_k          = req.top_k,
        vector_weight  = req.vector_weight,
        keyword_weight = req.keyword_weight,
    )

    if not results:
        raise HTTPException(status_code=404, detail="No relevant documents found.")

    # Step 2: Graph enrichment
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
            "doc_name"    : r.get("doc_name",    r.get("okf_context", {}).get("source", "")),
            "page_number" : r.get("page_number", r.get("okf_context", {}).get("page", "")),
            "category"    : r.get("doc_category",r.get("okf_context", {}).get("category","")),
            "section"     : r.get("section",     r.get("okf_context", {}).get("section","")),
            "hybrid_score": round(r.get("hybrid_score", 0.0), 4),
            "graph_enriched": r.get("graph_enriched", False),
        }
        for r in results
    ]

    return QueryResponse(answer=answer, sources=sources, chunks_used=len(results))


@app.post("/summarize", response_model=SummarizeResponse)
def summarize(req: SummarizeRequest):
    """
    Summarize a document by retrieving its chunks and passing to Claude.
    """
    _ensure_initialized()

    doc_chunks = _state["chunks_lookup"].get(req.doc_name)
    if not doc_chunks:
        # Try partial match
        matches = [d for d in _state["chunks_lookup"] if req.doc_name.lower() in d.lower()]
        if not matches:
            raise HTTPException(status_code=404, detail=f"Document '{req.doc_name}' not found.")
        doc_chunks = _state["chunks_lookup"][matches[0]]
        doc_name   = matches[0]
    else:
        doc_name = req.doc_name

    # Take a representative sample: first + middle + last chunks
    n = len(doc_chunks)
    if n <= req.max_chunks:
        sample = doc_chunks[:]
    else:
        # Distribute evenly: first third + middle + last third
        third  = req.max_chunks // 3
        first  = doc_chunks[:third]
        mid_s  = (n // 2) - (third // 2)
        middle = doc_chunks[mid_s : mid_s + third]
        last   = doc_chunks[-(req.max_chunks - 2 * third):]
        sample = first + middle + last

    context_block = _build_context_block([
        {**c, "okf_context": {
            "source": c["doc_name"], "category": c["doc_category"],
            "page": c["page_number"], "section": c.get("section",""),
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


@app.get("/docs/{doc_name}")
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


@app.get("/documents")
def list_documents():
    """List all indexed documents grouped by category."""
    _ensure_initialized()
    from collections import defaultdict
    by_cat: Dict[str, list] = defaultdict(list)
    for c in _state["chunks"]:
        n = c["doc_name"]
        cat = c["doc_category"]
        if n not in by_cat[cat]:
            by_cat[cat].append(n)
    return {cat: sorted(set(docs)) for cat, docs in by_cat.items()}
