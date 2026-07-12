"""
src/app.py — Milestone 3: Streamlit UI
=======================================
A polished web interface for the Legal & Tax RAG system.
Stack: Pinecone (vector) + Elasticsearch (keyword) + Ollama (embeddings) + Groq/Llama (LLM)

Run:
    streamlit run src/app.py
"""

import sys
import io
import os
import json
import time
from pathlib import Path
from typing import List, Dict, Any

# ─── UTF-8 fix (Windows console encoding for § characters) ──────────────────
import os as _os
if not _os.getenv("PYTEST_CURRENT_TEST") and hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import streamlit as st
from dotenv import load_dotenv

# Resolve .env from project root regardless of cwd
_PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(_PROJECT_ROOT / ".env", override=True)

# ─── Page config ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Legal & Tax RAG",
    page_icon="⚖️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Styling ─────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

.main-header {
    background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
    padding: 2rem 2.5rem;
    border-radius: 16px;
    margin-bottom: 1.5rem;
    box-shadow: 0 8px 32px rgba(0,0,0,0.3);
}
.main-header h1 { color: #e0e7ff; font-size: 2rem; font-weight: 700; margin: 0; }
.main-header p  { color: #94a3b8; margin: 0.5rem 0 0; font-size: 0.95rem; }

.answer-box {
    background: linear-gradient(135deg, #0f172a, #1e293b);
    border: 1px solid #334155;
    border-radius: 12px;
    padding: 1.5rem;
    color: #e2e8f0;
    line-height: 1.7;
    font-size: 0.95rem;
    margin: 1rem 0;
    box-shadow: 0 4px 16px rgba(0,0,0,0.2);
}
.source-card {
    background: #0f172a;
    border: 1px solid #1e40af;
    border-left: 4px solid #3b82f6;
    border-radius: 8px;
    padding: 0.75rem 1rem;
    margin: 0.4rem 0;
    color: #93c5fd;
    font-size: 0.875rem;
}
.source-card .score { color: #64748b; font-size: 0.8rem; }
.graph-badge {
    background: #1e3a5f;
    color: #60a5fa;
    border-radius: 4px;
    padding: 2px 6px;
    font-size: 0.75rem;
    margin-left: 8px;
}
.metric-card {
    background: #1e293b;
    border-radius: 10px;
    padding: 1rem;
    text-align: center;
    border: 1px solid #334155;
}
.metric-card h3 { color: #60a5fa; font-size: 1.5rem; margin: 0; }
.metric-card p  { color: #94a3b8; font-size: 0.8rem; margin: 0.25rem 0 0; }
.stTextInput > div > input { background: #1e293b; color: #e2e8f0; border: 1px solid #334155; border-radius: 8px; }
.stButton > button {
    background: linear-gradient(135deg, #1d4ed8, #2563eb);
    color: white; border: none; border-radius: 8px; font-weight: 600;
    padding: 0.5rem 1.5rem; font-size: 0.9rem;
    transition: all 0.2s;
}
.stButton > button:hover { transform: translateY(-1px); box-shadow: 0 4px 12px rgba(37,99,235,0.4); }
.warning-box {
    background: #1c1917; border: 1px solid #b45309;
    border-radius: 8px; padding: 1rem; color: #fbbf24; margin: 1rem 0;
}
</style>
""", unsafe_allow_html=True)


# ─── RAG initialization ───────────────────────────────────────────────────────

@st.cache_resource(show_spinner=False)
def initialize_rag():
    """Load chunks, build graph, build search index. Cached across sessions."""
    project_root = Path(__file__).parent.parent
    chunks_path  = project_root / "outputs" / "parsed_chunks.json"
    graph_path   = project_root / "outputs" / "citation_graph.json"

    sys.path.insert(0, str(project_root / "src"))
    from search    import build_index, hybrid_search
    from graph_rag import load_graph, build_citation_graph, save_graph, enrich_results_with_graph

    if not chunks_path.exists():
        return None, None, None, None, (
            "parsed_chunks.json not found. Run: `python src/parser.py` first."
        )

    with open(chunks_path, encoding="utf-8") as f:
        data   = json.load(f)
    chunks = data["chunks"]

    from collections import defaultdict
    lookup: Dict[str, list] = defaultdict(list)
    for c in chunks:
        lookup[c["doc_name"]].append(c)

    # Graph
    if graph_path.exists():
        try:
            G = load_graph(str(graph_path))
        except Exception:
            G = build_citation_graph(chunks)
            save_graph(G, str(graph_path))
    else:
        G = build_citation_graph(chunks)
        save_graph(G, str(graph_path))

    # Index (Pinecone + ES if available)
    try:
        build_index()
    except RuntimeError as exc:
        # Ollama not running is a hard stop
        return None, None, None, None, str(exc)

    return chunks, dict(lookup), G, data["metadata"], None


def run_query(question: str, top_k: int, use_graph: bool,
              chunks, lookup, G, meta) -> Dict[str, Any]:
    """Execute hybrid search + LLM call."""
    import sys, os
    sys.path.insert(0, str(Path(__file__).parent))
    from search    import hybrid_search
    from graph_rag import enrich_results_with_graph
    from openai import OpenAI

    try:
        results = hybrid_search(question, top_k=top_k)
    except Exception as exc:
        return {"error": f"Search error: {exc}", "sources": [], "chunks_used": 0}

    if not results:
        return {"answer": "No relevant documents found.", "sources": [], "chunks_used": 0}

    if use_graph and G:
        try:
            results = enrich_results_with_graph(results, G, lookup)
        except Exception:
            pass  # graph enrichment is optional

    # Build context
    ctx_lines = []
    for i, r in enumerate(results, 1):
        okf = r.get("okf_context", {})
        src = okf.get("source", r.get("doc_name", ""))
        pg  = okf.get("page",   r.get("page_number", ""))
        sec = okf.get("section", r.get("section", ""))
        txt = okf.get("content", r.get("text", ""))
        hdr = f"[CONTEXT {i}] Source: {src}, Page: {pg}"
        if sec: hdr += f", Section: {sec}"
        ctx_lines.append(hdr)
        ctx_lines.append(txt)
        ctx_lines.append("")
    context_block = "\n".join(ctx_lines)

    system_prompt = """You are a legal research assistant specializing in US tax law and legal analysis.

CRITICAL RULES:
1. Answer using the provided context chunks below to the best of your ability.
2. After each claim, cite the source: [Source: {doc_name}, Page {page_number}]
3. If the answer is not completely in the context, provide whatever relevant information you can find from the documents, clearly stating what is covered and what is not.
4. Do NOT use external knowledge. Do NOT hallucinate facts, section numbers, or case outcomes."""

    user_msg = f"""Context chunks:

{context_block}

---
Question: {question}

Answer using only the context above, citing each source."""

    api_key  = os.getenv("LLM_API_KEY", "")
    base_url = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    model    = os.getenv("LLM_MODEL",   "llama-3.3-70b-versatile")

    if not api_key:
        return {
            "error": "LLM_API_KEY not set in .env (set your Groq API key).",
            "sources": [], "chunks_used": len(results)
        }

    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        resp   = client.chat.completions.create(
            model       = model,
            temperature = 0,
            messages    = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg}
            ],
        )
        answer = resp.choices[0].message.content
    except Exception as exc:
        return {
            "error": f"LLM call failed: {exc}\n\n**Base URL:** `{base_url}`\n**Model:** `{model}`",
            "sources": [], "chunks_used": len(results)
        }

    sources = [{
        "doc_name"     : r.get("doc_name", ""),
        "page_number"  : r.get("page_number", ""),
        "category"     : r.get("doc_category", ""),
        "section"      : r.get("section", ""),
        "hybrid_score" : round(r.get("hybrid_score", 0.0), 4),
        "graph_enriched": r.get("graph_enriched", False),
    } for r in results]

    return {"answer": answer, "sources": sources, "chunks_used": len(results)}


# ─── UI ───────────────────────────────────────────────────────────────────────

def render_header():
    st.markdown("""
    <div class="main-header">
        <h1>⚖️ Legal & Tax RAG System</h1>
        <p>AI-powered research assistant for US Tax Law & Legal documents — answers grounded in source documents with precise citations.</p>
    </div>
    """, unsafe_allow_html=True)


def render_sidebar(meta):
    with st.sidebar:
        st.markdown("### ⚙️ Configuration")

        top_k = st.slider("Retrieved chunks (top-k)", 3, 10, 5)
        use_graph = st.checkbox("Graph RAG enrichment", value=True,
                                help="Enrich results by following citation links between judgments and acts.")

        st.markdown("---")
        st.markdown("### 🔌 Service Status")

        # Ollama check
        import urllib.request as _ureq
        try:
            _ureq.urlopen("http://localhost:11434/api/tags", timeout=2)
            st.success("🟢 Ollama (embeddings): Running")
        except Exception:
            st.error("🔴 Ollama: NOT running — `ollama serve`")

        # Elasticsearch check
        import socket as _sock
        try:
            s = _sock.create_connection(("localhost", 9200), timeout=1.5)
            s.close()
            st.success("🟢 Elasticsearch: Running")
        except Exception:
            st.warning("🟡 Elasticsearch: Not running (keyword search disabled)")

        # Groq / LLM
        llm_key = os.getenv("LLM_API_KEY", "")
        llm_model = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
        if llm_key:
            st.success(f"🟢 LLM: {llm_model[:30]}")
        else:
            st.error("🔴 LLM_API_KEY not set")

        st.markdown("---")
        st.markdown("### 📊 Index Stats")
        if meta:
            cols = st.columns(2)
            cols[0].metric("Total Chunks", f"{meta.get('total_chunks',0):,}")
            cols[1].metric("Documents",    f"{meta.get('total_docs',0):,}")
            cats = meta.get("categories", {})
            for cat, n in cats.items():
                st.markdown(f"- **{cat}**: {n:,} chunks")
        st.markdown("---")
        st.markdown("### 🔗 Quick Links")
        st.markdown("[FastAPI Docs](http://localhost:8000/docs)  \n"
                    "[Health Check](http://localhost:8000/health)")

    return top_k, use_graph


def render_query_tab(chunks, lookup, G, meta, top_k, use_graph):
    st.markdown("### 🔍 Ask a Legal or Tax Question")

    with st.form("query_form"):
        question = st.text_area(
            "Your question",
            placeholder="e.g., What is the SALT deduction cap under §164 for 2025? "
                        "Or: What was the holding in Blackman v. Commissioner regarding casualty losses?",
            height=100,
        )
        submitted = st.form_submit_button("🔍 Search & Answer", use_container_width=True)

    # Example queries
    st.markdown("**💡 Example queries:**")
    examples = [
        "What is the SALT deduction cap under §164 for tax year 2025?",
        "Under §165(c)(3), can a taxpayer deduct a casualty loss from an intentional fire?",
        "What factors does the Tax Court consider to determine if a real estate seller is a dealer or investor?",
        "What is the accuracy-related penalty rate under §6662?",
        "How does Foote v. Commissioner treat the sale of academic tenure?",
    ]
    cols = st.columns(3)
    for i, ex in enumerate(examples):
        if cols[i % 3].button(ex[:50] + "…", key=f"ex_{i}"):
            question = ex
            submitted = True

    if submitted and question.strip():
        with st.spinner("🔎 Searching documents and generating answer…"):
            t0     = time.time()
            result = run_query(question, top_k, use_graph, chunks, lookup, G, meta)
            elapsed = time.time() - t0

        # Show error if any
        if result.get("error"):
            st.error(f"❌ {result['error']}")
            if result.get("chunks_used", 0) > 0:
                st.info(f"ℹ️ Search found {result['chunks_used']} relevant chunks but LLM call failed.")
        else:
            # Answer
            st.markdown(f"#### 📝 Answer  <span style='color:#64748b;font-size:0.8rem;'>({elapsed:.1f}s)</span>",
                        unsafe_allow_html=True)
            st.markdown(f'<div class="answer-box">{result["answer"]}</div>', unsafe_allow_html=True)

        # Sources
        if not result.get("error") and result.get("sources"):
            st.markdown(f"#### 📚 Sources ({result['chunks_used']} chunks retrieved)")
            for i, src in enumerate(result["sources"], 1):
                graph_badge = '<span class="graph-badge">📊 Graph-enriched</span>' \
                              if src.get("graph_enriched") else ""
                st.markdown(
                    f'<div class="source-card">'
                    f'<strong>[{i}]</strong> {src["doc_name"][:80]}{graph_badge}<br>'
                    f'📄 Page {src["page_number"]}  •  🏷️ {src["category"]}'
                    + (f'  •  §{src["section"][:30]}' if src.get("section") else '') +
                    f'<br><span class="score">Relevance: {src["hybrid_score"]:.3f}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
    elif submitted:
        st.warning("Please enter a question.")


def render_summarize_tab(chunks, lookup, G, meta):
    st.markdown("### 📄 Document Summarizer")

    if not lookup:
        st.error("No documents indexed.")
        return

    doc_names = sorted(lookup.keys())

    # Filter by category
    cat_filter = st.selectbox("Filter by category", ["All", "Acts", "Judgments", "POV", "Tax Docs"])
    if cat_filter != "All":
        cat_map = {"Acts": "Acts", "Judgments": "Judgments", "POV": "POV", "Tax Docs": "Tax Docs"}
        doc_names = [d for d in doc_names
                     if lookup[d][0]["doc_category"] == cat_map.get(cat_filter, cat_filter)]

    selected_doc = st.selectbox("Select document to summarize", doc_names)
    max_chunks   = st.slider("Max chunks to summarize", 5, 20, 10)

    if st.button("📋 Generate Summary", use_container_width=True):
        with st.spinner("Generating summary…"):
            import os, sys
            sys.path.insert(0, str(Path(__file__).parent))
            from openai import OpenAI

            doc_chunks = lookup.get(selected_doc, [])[:max_chunks]
            ctx_lines  = []
            for i, c in enumerate(doc_chunks, 1):
                ctx_lines.append(f"[Excerpt {i}] Page {c['page_number']}: {c['text']}")
                ctx_lines.append("")
            context = "\n".join(ctx_lines)

            system = ("You are a legal research assistant. "
                      "Summarize these document excerpts concisely. "
                      "Include: document type, key provisions/findings, important numbers/thresholds. "
                      "Only use information from the provided excerpts.")
            user   = f"Summarize this document:\n\n{context}"

            api_key  = os.getenv("LLM_API_KEY", "")
            base_url = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
            model    = os.getenv("LLM_MODEL",   "llama-3.3-70b-versatile")

            client = OpenAI(api_key=api_key, base_url=base_url)
            resp   = client.chat.completions.create(
                model=model,
                temperature=0,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user}
                ],
            )
            summary = resp.choices[0].message.content

        st.markdown(f"#### Summary: *{selected_doc[:70]}*")
        st.markdown(f'<div class="answer-box">{summary}</div>', unsafe_allow_html=True)
        st.caption(f"Based on {len(doc_chunks)} chunks from {lookup[selected_doc][0]['doc_category']}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    render_header()

    with st.spinner("Loading RAG system (first load may take ~60s for embedding model)…"):
        chunks, lookup, G, meta, error = initialize_rag()

    if error:
        st.error(f"❌ Initialization error: {error}")
        return

    top_k, use_graph = render_sidebar(meta)

    tab1, tab2 = st.tabs(["🔍 Q&A Research", "📄 Document Summary"])

    with tab1:
        render_query_tab(chunks, lookup, G, meta, top_k, use_graph)

    with tab2:
        render_summarize_tab(chunks, lookup, G, meta)


if __name__ == "__main__":
    main()
