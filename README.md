# ⚖️ Legal & Tax RAG System

AI-powered research assistant for US Tax Law & Legal documents — answers grounded in source documents with **precise citations**.

---

## Stack

| Layer | Tool |
|-------|------|
| Embeddings | Ollama `nomic-embed-text` (local, 768-dim) |
| Vector Store | Pinecone (serverless, cosine) |
| Keyword Search | Elasticsearch 8.x BM25 (Docker, optional) |
| LLM | Groq `llama-3.3-70b-versatile` |
| UI | Streamlit |
| API | FastAPI |
| Graph | NetworkX (Judgment → Act citation graph) |

---

## Quick Start

### 1. Prerequisites

```bash
# Python 3.10+
pip install -r requirements.txt

# Ollama (local embeddings)
# Download from: https://ollama.ai
ollama serve
ollama pull nomic-embed-text
```

### 2. Configure `.env`

```bash
cp .env.example .env
# Fill in your PINECONE_API_KEY and LLM_API_KEY (Groq)
```

### 3. (Optional) Start Elasticsearch for full hybrid search

```bash
docker-compose up -d
# Wait ~30s for ES to be ready
```

### 4. Parse PDFs → build chunks

```bash
python src/parser.py
```

### 5. Build search index (Pinecone + Elasticsearch)

```bash
python src/search.py --build
```

### 6. Run Streamlit UI

```bash
streamlit run src/app.py
# Opens at http://localhost:8501
```

### 7. (Optional) Run FastAPI backend

```bash
uvicorn src.api:app --reload --port 8000
# Docs at http://localhost:8000/docs
```

---

## Evaluation & Performance Metrics

We evaluated the system against a **90-document Golden Set** representing real-world legal queries across Tax Acts, Court Judgments, and POVs.

**Key Results (Live Test):**
- **Retrieval Top-1 Accuracy:** 83.3%
- **Retrieval Top-5 Accuracy:** 93.3%
- **Average Faithfulness:** 3.89 / 5 (LLM-as-a-Judge)

*Note: The system achieved **100% Top-1 Accuracy** specifically on the 'Judgments' category, proving the effectiveness of the Graph RAG citation extraction.*

To reproduce the evaluation:
```bash
# Run against full Golden Set (90 Q&A pairs)
python src/evaluate.py

# Limit rows for quick test
python src/evaluate.py --max_rows 20
```

Detailed reports are generated and saved to:
- `outputs/evaluation_report.json`
- `docs/Evaluation_Report.md` (Included in this repo as official proof)

---

## Unit Tests

```bash
pip install pytest
python -m pytest tests/test_system.py -v
```

---

## Architecture

See [`docs/architecture.md`](docs/architecture.md) for the full system diagram and data flow.

---

## Project Structure

```
Legal_Tax_RAG_System/
├── data/
│   ├── acts/          # 30 PDFs — 26 USC sections
│   ├── judgement/     # 30 PDFs — Tax Court cases
│   ├── pov/           # 30 PDFs — Tax Foundation POV
│   └── Tax/           # 10 PDFs — IRS Publications
├── src/
│   ├── parser.py      # M1: PDF ingestion + chunking
│   ├── search.py      # M2: Hybrid Pinecone + Elasticsearch
│   ├── graph_rag.py   # M2: Citation graph (NetworkX)
│   ├── api.py         # M3: FastAPI backend
│   ├── app.py         # M3: Streamlit UI
│   └── evaluate.py    # M4: Evaluation pipeline
├── tests/
│   └── test_system.py # Unit + integration tests
├── outputs/           # Generated files
├── docs/              # Architecture + eval reports
├── docker-compose.yml # Elasticsearch
├── .env.example       # Template (safe to commit)
└── requirements.txt
```
