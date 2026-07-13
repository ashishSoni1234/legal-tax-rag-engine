# ⚖️ Legal & Tax RAG System (Vercel Edition)

An enterprise-grade AI research assistant for US Tax Law & Legal documents. Answers are **always grounded in source documents** with precise citations — no hallucinations.

---

## 🏆 Evaluation Results (90-Question Golden Set)

| Metric | Score |
|--------|-------|
| **Retrieval Top-1 Accuracy** | **83.3%** |
| **Retrieval Top-3 Accuracy** | **88.9%** |
| **Retrieval Top-5 Accuracy** | **93.3%** |
| **Average Faithfulness (LLM-as-Judge)** | **3.89 / 5** |
| Judgments Category Top-1 Accuracy | **100%** |

> Full report: [`docs/Evaluation_Report.md`](docs/Evaluation_Report.md)

---

## 🏗️ Architecture

```
PDF Documents
    │
    ▼
[1] Parser (PyMuPDF)
    │  Extracts text, preserves page numbers & sections -> parsed_chunks.json
    ▼
[2] Hybrid Search Indexing
    ├─► Pinecone  (Vector DB — semantic search via gemini-embedding-001)
    ├─► rank_bm25 (In-memory BM25 keyword search — No Docker required)
    └─► NetworkX  (Citation Graph — Graph RAG)
    │
    ▼
[3] Query Pipeline
    │  User query → embed → hybrid search → graph enrichment
    ▼
[4] LLM (Groq llama-3.3-70b-versatile)
    │  Answers ONLY from retrieved chunks, with citations
    ▼
[5] Next.js UI / FastAPI
    │  Displays answer + exact source document + page number + citation accordion
```

---

## 🛠️ Tech Stack

| Layer | Tool |
|-------|------|
| Embeddings | Google Gemini `gemini-embedding-001` (Cloud, 768-dim) |
| Vector Store | Pinecone (serverless, cosine similarity) |
| Keyword Search | In-memory `rank_bm25` (Vercel-compatible) |
| LLM | Groq `llama-3.3-70b-versatile` |
| Graph RAG | NetworkX (Judgment → Act citation mapping) |
| UI | Next.js (React 19) |
| API | FastAPI (Serverless via Vercel / Mangum) |

---

## 🚀 Quick Start (Step-by-Step)

### Step 1: Clone the repository

```bash
git clone https://github.com/ashishSoni1234/legal-tax-rag-engine.git
cd legal-tax-rag-engine
```

### Step 2: Install Dependencies

You will need both Python (for backend/indexing) and Node.js (for frontend).

```bash
# Python dependencies
pip install -r requirements.txt

# Node.js dependencies
npm install
```

### Step 3: Configure API Keys

Create a `.env` file in the root directory:
```bash
cp .env.example .env
```

Then open `.env` and fill in **3 required keys**:

```env
# 1. Get FREE from: https://console.groq.com
LLM_API_KEY=your_groq_api_key_here

# 2. Get FREE from: https://app.pinecone.io
PINECONE_API_KEY=your_pinecone_api_key_here

# 3. Get FREE from: https://aistudio.google.com
GEMINI_API_KEY=your_gemini_api_key_here
```

### Step 4: Parse PDFs and build the search index

> ⚠️ This step only needs to be done ONCE locally. It reads all PDFs, builds JSON chunks, and uploads vectors to Pinecone.

```bash
# Build Pinecone index (uploads embeddings) and chunks
python src/search.py --build
```

### Step 5: Run the Full Stack Locally

The Next.js dev server is configured to proxy `/api` calls to the FastAPI backend running on port 8000.

**Terminal 1 (Backend):**
```bash
python -m uvicorn api.index:app --reload --port 8000
```

**Terminal 2 (Frontend):**
```bash
npm run dev
# Opens automatically at: http://localhost:3000
```

---

## 🌍 Vercel Deployment

This project is fully ready for Vercel deployment:
1. Push your code to GitHub.
2. Import the project in Vercel.
3. Add `LLM_API_KEY`, `PINECONE_API_KEY`, and `GEMINI_API_KEY` to Vercel Environment Variables.
4. Deploy! `vercel.json` automatically handles mapping Next.js frontend and Python FastAPI backend routes.

---

## 📁 Project Structure

```
legal-tax-rag-engine/
├── api/
│   └── index.py       # Vercel Serverless FastAPI Backend
├── app/
│   └── page.tsx       # Next.js Chat UI
├── src/
│   ├── parser.py      # PDF ingestion & chunking
│   ├── search.py      # Hybrid Pinecone + BM25 (Gemini embeddings)
│   └── graph_rag.py   # Citation graph (NetworkX)
├── outputs/
│   ├── parsed_chunks.json      # Generated chunk data
│   └── citation_graph.json     # Generated graph data
├── vercel.json        # Vercel deployment configuration
├── next.config.ts     # Next.js configuration & API proxy
├── package.json       # Node.js dependencies
└── requirements.txt   # Python dependencies
```

---

## 🔑 API Keys Summary

| Key | Where to Get | Required? |
|-----|-------------|-----------|
| `PINECONE_API_KEY` | [app.pinecone.io](https://app.pinecone.io) | ✅ Yes |
| `LLM_API_KEY` (Groq) | [console.groq.com](https://console.groq.com) | ✅ Yes |
| `GEMINI_API_KEY` | [aistudio.google.com](https://aistudio.google.com) | ✅ Yes |
