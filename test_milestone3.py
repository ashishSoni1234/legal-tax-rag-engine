import os
import sys
import json
import io
from pathlib import Path

# Fix stdout encoding for Windows
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from src.app import initialize_rag, run_query
from src.api import _ensure_initialized, query, summarize, QueryRequest, SummarizeRequest

def test_features():
    print("Initializing RAG...")
    chunks, lookup, G, meta, error = initialize_rag()
    if error:
        print(f"Error initializing: {error}")
        return
    
    # 1. Test Q&A Interface
    question = "What is the penalty for failure to collect and pay over tax under section 6672?"
    print(f"\n--- Testing Q&A ---")
    print(f"Question: {question}")
    
    result = run_query(question, 5, True, chunks, lookup, G, meta)
    if result.get("error"):
        print(f"Error: {result['error']}")
    else:
        print("\nAnswer:")
        print(result["answer"])
        print("\nCitations/References provided in sources:")
        for src in result.get("sources", []):
            print(f"- Doc: {src['doc_name']}, Page: {src['page_number']}, Sec: {src['section']}")
            
    # 2. Test Summarization
    doc_name = "26 U.S. Code  6672 - Failure to collect and pay over tax, or attempt to evade or defeat tax _ U.S. Code _ US Law _ LII _ Legal Information Institute"
    print(f"\n--- Testing Summarization ---")
    print(f"Document: {doc_name}")
    
    _ensure_initialized()
    req = SummarizeRequest(doc_name=doc_name, max_chunks=5)
    try:
        sum_resp = summarize(req)
        print("\nSummary:")
        print(sum_resp.summary)
        print(f"\nUsed {sum_resp.chunks_used} chunks for summarization.")
    except Exception as e:
        print(f"Error summarizing: {e}")

if __name__ == '__main__':
    test_features()
