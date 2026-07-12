import pandas as pd
import json
import re

# Load Golden Set
try:
    df = pd.read_excel('Golden_Set.xlsx')
    print(f"Total Rows: {len(df)}")
    print(f"Columns: {list(df.columns)}")
except Exception as e:
    print(f"Error reading Excel: {e}")
    exit(1)

# Load Chunks
try:
    with open('outputs/parsed_chunks.json', encoding='utf-8') as f:
        chunks_data = json.load(f)['chunks']
        
    doc_names = set(c['doc_name'] for c in chunks_data)
    print(f"Total Documents in Database: {len(doc_names)}")
except Exception as e:
    print(f"Error reading chunks: {e}")
    exit(1)

# Check 1: Schema
expected_cols = ['id', 'category', 'query', 'ground_truth_answer', 'source_document', 'difficulty']
actual_cols = [c.lower().strip().replace(' ', '_').replace('/', '_') for c in df.columns]
missing_cols = [c for c in expected_cols if c not in actual_cols]
if missing_cols:
    print(f"\n[GAP] Missing Columns: {missing_cols}")
else:
    print("\n[OK] Schema is correct.")

# Check 2: Source Documents Validity
df_cols = df.columns
src_col = [c for c in df_cols if c.lower().strip().replace(' ', '_') == 'source_document'][0]

def normalise_doc_name(name):
    name = str(name)
    name = re.sub(r"\.pdf$", "", name, flags=re.IGNORECASE)
    name = name.lower()
    name = re.sub(r"[^a-z0-9]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name

norm_db_docs = {normalise_doc_name(d): d for d in doc_names}

missing_docs = []
for idx, row in df.iterrows():
    src = str(row[src_col])
    norm_src = normalise_doc_name(src)
    
    # Check if exact match or substring match
    match = False
    if norm_src in norm_db_docs:
        match = True
    else:
        for db_doc, orig_db_doc in norm_db_docs.items():
            if norm_src in db_doc or db_doc in norm_src:
                match = True
                break
    if not match:
        missing_docs.append((idx+2, src))

if missing_docs:
    print(f"\n[GAP] Found {len(missing_docs)} source documents in Golden Set that DO NOT exist in the database!")
    for m in missing_docs[:10]:
        print(f"  Row {m[0]}: {m[1]}")
    if len(missing_docs) > 10:
        print("  ... and more.")
else:
    print("\n[OK] All source documents in the Golden Set exist in the database.")

# Check 3: Check Empty Answers or Queries
query_col = [c for c in df_cols if 'query' in c.lower()][0]
ans_col = [c for c in df_cols if 'ground_truth' in c.lower()][0]

empty_queries = df[df[query_col].isna() | (df[query_col].astype(str).str.strip() == '')]
empty_answers = df[df[ans_col].isna() | (df[ans_col].astype(str).str.strip() == '')]

if len(empty_queries) > 0:
    print(f"\n[GAP] Found {len(empty_queries)} rows with empty queries.")
if len(empty_answers) > 0:
    print(f"\n[GAP] Found {len(empty_answers)} rows with empty ground truth answers.")

# Print Summary of Categories
cat_col = [c for c in df_cols if 'category' in c.lower()][0]
print("\nCategory Distribution in Golden Set:")
print(df[cat_col].value_counts())
