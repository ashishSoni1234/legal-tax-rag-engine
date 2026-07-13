# Legal & Tax RAG System — Architecture

## System Architecture

```mermaid
graph TD
    subgraph Input["📂 Input Layer"]
        A[104 PDF Documents<br/>acts/ • judgement/ • pov/ • Tax/]
    end

    subgraph M1["⚙️ Milestone 1: Ingestion Pipeline"]
        B[parser.py<br/>PyMuPDF page-by-page extraction]
        C[Chunker<br/>600 tokens / 75 overlap<br/>tiktoken cl100k_base]
        D[(parsed_chunks.json<br/>4,691 chunks<br/>9.4 MB)]
    end

    subgraph M2["🔍 Milestone 2: Search Architecture"]
        E[Google Gemini API<br/>gemini-embedding-001<br/>768-dim embeddings]
        F[(Pinecone Cloud<br/>ANN vector index<br/>cosine similarity)]
        G[rank_bm25<br/>BM25 keyword index<br/>in-memory]
        H[Hybrid Scorer<br/>0.6 × vector + 0.4 × keyword]
        I[graph_rag.py<br/>NetworkX citation graph<br/>Judgment → Act edges]
        J[(citation_graph.json<br/>edge list)]
        K[OKF Context Wrapper<br/>source • category • page • section • content]
    end

    subgraph M3["🤖 Milestone 3: Q&A + Summarization"]
        L[FastAPI Backend<br/>api.py<br/>POST /query • POST /summarize]
        M[Groq — llama-3.3-70b-versatile<br/>temp=0 • strict grounding prompt<br/>citation format enforced]
        N[Next.js UI<br/>app/page.tsx<br/>Dark theme • Q&A tab • Summary tab]
    end

    subgraph M4["📊 Milestone 4: Evaluation"]
        O[evaluate.py<br/>Golden_Set.xlsx<br/>135 Q&A pairs]
        P[Retrieval Accuracy<br/>Top-1 / Top-3 / Top-5]
        Q[LLM-as-Judge<br/>Faithfulness 1–5<br/>Groq llama-3.3-70b]
        R[(evaluation_report.json<br/>Evaluation_Report.md)]
    end

    A --> B --> C --> D
    D --> E --> F
    D --> G
    F --> H
    G --> H
    D --> I --> J
    H --> I
    I --> K
    K --> L
    L --> M --> L
    L --> N
    D --> O
    O --> P
    O --> Q
    P --> R
    Q --> R

    style Input fill:#0f172a,stroke:#1e40af,color:#e2e8f0
    style M1 fill:#0f172a,stroke:#065f46,color:#e2e8f0
    style M2 fill:#0f172a,stroke:#1e3a5f,color:#e2e8f0
    style M3 fill:#0f172a,stroke:#4c1d95,color:#e2e8f0
    style M4 fill:#0f172a,stroke:#7f1d1d,color:#e2e8f0
```

---

## Data Flow

```
PDF Files
  │
  ▼
[parser.py]  →  PyMuPDF (text per page)  →  tiktoken chunker  →  parsed_chunks.json
                                                                          │
                                          ┌───────────────────────────────┤
                                          │                               │
                                          ▼                               ▼
                                    [search.py]                    [graph_rag.py]
                                  Embeddings: Google Gemini API   Citation graph
                                  (gemini-embedding-001, 768-dim) NetworkX DiGraph
                                  Vector:   Pinecone               ↕ JSON edge list
                                  Keyword:  rank_bm25 (In-memory)
                                  Hybrid:   0.6v + 0.4k
                                          │                               │
                                          └───────────────────────────────┘
                                                          │
                                                          ▼
                                                  OKF Context Wrapper
                                          {source, category, page, section, content}
                                                          │
                                                          ▼
                                               [api.py / FastAPI]
                                                          │
                                                  ┌───────┴────────┐
                                                  ▼                ▼
                                            POST /query     POST /summarize
                                                  │
                                                  ▼
                                     Groq — llama-3.3-70b-versatile (temp=0)
                                     STRICT GROUNDING PROMPT + citations
                                                  │
                                       ┌──────────┴──────────┐
                                       ▼                     ▼
                                  Answer text         Source citations
                              [doc_name, page, score]
                                       │
                                       ▼
                               [Next.js UI - app/page.tsx]
```

---

## Key Design Decisions

| Component | Choice | Rationale |
|-----------|--------|-----------|
| PDF parser | PyMuPDF (fitz) | Fastest, handles complex legal document layouts |
| Tokenizer | tiktoken cl100k_base | Accurate token counting for chunk sizing |
| Chunk size | 600 tokens / 75 overlap | Balances context richness vs. retrieval precision |
| Embeddings | Google Gemini API (768-dim) | High quality embeddings via API |
| Vector store | Pinecone (serverless, cosine) | Cloud-hosted ANN, handles 4,691+ vectors reliably |
| Keyword search | rank_bm25 (BM25Okapi) | In-memory exact legal term matching (§ numbers, IRC citations, case names) |
| Hybrid weights | 0.6 vector / 0.4 keyword | Configurable; keyword critical for exact legal references |
| Graph RAG | NetworkX DiGraph | Links Judgments→Acts via §-references; enriches retrieval context |
| LLM | Groq llama-3.3-70b-versatile | High-speed inference, OpenAI-compatible API, temperature=0 |
| Anti-hallucination | System prompt + OKF | Every claim must be traceable to a source chunk |
| Evaluation | Golden Set (135 Q&A) + LLM-as-judge | Measures retrieval accuracy (Top-1/3/5) + faithfulness (1-5) |

---

## Stack Summary

| Layer | Tool | Type |
|-------|------|------|
| Embeddings | Google Gemini API | Cloud API |
| Vector DB | Pinecone (serverless) | Cloud |
| Keyword Search | rank_bm25 | In-memory |
| LLM | Groq llama-3.3-70b-versatile | Cloud API |
| UI | Next.js / React | Local / Vercel |
| API | FastAPI + Uvicorn | Local |
| Graph | NetworkX | In-memory |

---

## File Structure

```
Legal_Tax_RAG_System/
├── data/
│   ├── acts/            (30 PDF files — 26 USC sections)
│   ├── judgement/       (30 PDF files — Tax Court cases)
│   ├── pov/             (30 PDF files — Tax Foundation POV)
│   └── Tax/             (10 PDF files — IRS Publications)
├── src/
│   ├── parser.py        # M1: PDF ingestion + chunking
│   ├── search.py        # M2: Hybrid vector (Pinecone) + keyword (ES) search + OKF
│   ├── graph_rag.py     # M2: Citation graph (NetworkX)
│   ├── api.py           # M3: FastAPI backend
│   └── evaluate.py      # M4: Evaluation pipeline
├── app/                 # M3: Next.js Frontend UI pages
├── tests/
│   └── test_system.py   # Unit + integration tests
├── outputs/
│   ├── parsed_chunks.json      (4,691 chunks, 9.4 MB)
│   ├── citation_graph.json     (NetworkX edge list)
│   └── evaluation_report.json  (M4 output)
├── docs/
│   ├── architecture.md         (this file)
│   └── Evaluation_Report.md    (M4 output)
├── Golden_Set.xlsx     (135 Q&A pairs for evaluation)
├── requirements.txt
├── .env.example        (template — safe to commit)
└── .env                (real keys — gitignored)
```
