"""
Extract text from all PDFs in the Legal_Tax_RAG_System folders.
Saves extracted text to a single JSON file for review.
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import fitz  # PyMuPDF
import json
import os

BASE = r"c:\Users\Ls Computer\Downloads\Legal_Tax_RAG_System\data"
FOLDERS = {
    "acts": os.path.join(BASE, "acts"),
    "judgements": os.path.join(BASE, "judgement"),
    "pov": os.path.join(BASE, "pov"),
    "tax_docs": os.path.join(BASE, "Tax"),
}

OUTPUT = r"c:\Users\Ls Computer\Downloads\Legal_Tax_RAG_System\pdf_extracts.json"

results = {}

for category, folder_path in FOLDERS.items():
    results[category] = {}
    files = sorted(os.listdir(folder_path))
    pdf_files = [f for f in files if f.lower().endswith(".pdf")]
    print(f"\n{'='*60}")
    print(f"Category: {category} — {len(pdf_files)} files")
    print(f"{'='*60}")
    
    for fname in pdf_files:
        fpath = os.path.join(folder_path, fname)
        try:
            doc = fitz.open(fpath)
            num_pages = len(doc)
            
            # Extract text from first 6 pages (sufficient for Q&A generation)
            pages_text = {}
            for page_num in range(min(num_pages, 8)):
                page = doc[page_num]
                text = page.get_text("text")
                if text.strip():
                    pages_text[page_num + 1] = text[:3000]  # cap per page
            
            doc.close()
            
            results[category][fname] = {
                "total_pages": num_pages,
                "pages_text": pages_text
            }
            print(f"  OK: {fname[:70]} ({num_pages} pages)")
        except Exception as e:
            print(f"  ERROR: {fname}: {e}")
            results[category][fname] = {"error": str(e)}

with open(OUTPUT, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

print(f"\n\n✅ Extraction complete. Output saved to: {OUTPUT}")
total = sum(len(v) for v in results.values())
print(f"Total files processed: {total}")
