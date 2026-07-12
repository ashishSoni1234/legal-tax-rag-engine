"""
Show condensed content from the extracted PDFs for Q&A generation.
Output category by category.
"""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

with open("pdf_extracts.json", encoding="utf-8") as f:
    data = json.load(f)

import sys
category = sys.argv[1] if len(sys.argv) > 1 else "acts"
docs = data.get(category, {})

for fname, info in docs.items():
    print(f"\n{'='*70}")
    print(f"FILE: {fname}")
    print(f"Pages: {info.get('total_pages', '?')}")
    print(f"{'='*70}")
    pages = info.get("pages_text", {})
    for pg, txt in pages.items():
        print(f"\n--- Page {pg} ---")
        print(txt[:2000])
        print()
