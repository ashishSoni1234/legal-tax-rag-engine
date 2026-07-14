# Approach and Architecture Document

This document outlines the system architecture, technology stack, and search strategies employed in the Legal & Tax RAG (Retrieval-Augmented Generation) System.

## 1. System Architecture

The application is built on a modern, decoupled architecture featuring a Python/FastAPI backend and a React/Next.js frontend. The pipeline is designed to extract, index, and query complex legal and tax documents with high accuracy.

### Architecture Flow:
1. **Document Ingestion (Parser)**: PDF documents are ingested using `PyMuPDF`. The parser extracts text while preserving metadata such as page numbers and sections.
2. **Hybrid Search Indexing**: The extracted chunks are indexed in two ways:
   - **Vector Store**: Uploaded to Pinecone using Google Gemini embeddings (`gemini-embedding-001`, 768-dim) for semantic understanding.
   - **Keyword Store**: Indexed locally using `rank_bm25` for exact keyword matching.
3. **Graph RAG (Citation Graph)**: A citation graph is built using `NetworkX` to map relationships (e.g., Judgments citing Acts) for enriched context retrieval.
4. **Query Pipeline**: User queries are embedded and searched against both the Vector DB and Keyword index. Results are optionally enriched by the citation graph.
5. **Generation**: The retrieved, highly relevant chunks are fed to the LLM (`Groq llama-3.3-70b-versatile`) with strict instructions to generate answers grounded *only* in the provided text.

## 2. Technology Stack

| Component | Technology | Purpose |
|---|---|---|
| **Frontend** | Next.js, React, Tailwind CSS | UI/UX, chat interface, document viewing |
| **Backend API** | FastAPI, Python | Core API, search logic, LLM integration |
| **LLM Inference** | Groq (`llama-3.3-70b-versatile`) | Fast text generation, summarization |
| **Embeddings** | Google Gemini API | Generating semantic embeddings |
| **Vector Database** | Pinecone | Serverless, highly scalable vector search |
| **Keyword Search** | `rank_bm25` | Lexical search for exact legal terms |
| **Graph Context** | NetworkX | Citation mapping between documents |
| **PDF Parsing** | PyMuPDF | Text extraction from legal PDFs |
| **Hosting** | Vercel | Serverless deployment of both Frontend and Backend |

## 3. Search Approach and Weightage

To achieve high retrieval accuracy, the system implements a **Hybrid Search** strategy, combining Semantic (Vector) and Lexical (Keyword) search. 

### Weightage Configuration:
During a query, the scores from Pinecone (Vector) and BM25 (Keyword) are normalized and combined using the following weights:
- **Vector Search Weight**: `0.6` (60%)
- **Keyword Search Weight**: `0.4` (40%)
- **Top-K Retrieval**: `5` chunks

**Why this weightage?** 
Legal and tax documents often contain highly specific terminology (e.g., "Section 170(h)"). Semantic search alone might miss exact term matches, while keyword search alone doesn't understand context. A `0.6 / 0.4` split leans slightly towards semantic meaning while maintaining a strong signal for exact legal jargon.

### Graph Enrichment:
If a retrieved "Judgment" chunk heavily cites a specific "Act", the Graph RAG component pulls in related chunks from the Act to provide the LLM with complete context, dramatically improving answer quality for cross-referenced documents.
