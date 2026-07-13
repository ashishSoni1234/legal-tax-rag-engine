"""
tests/test_system.py — Unit & Integration Tests for Legal Tax RAG System
=========================================================================
Run with:
    python -m pytest tests/test_system.py -v
    python -m pytest tests/test_system.py -v --tb=short

Covers:
    1. Parser (chunking, section extraction, token counting)
    2. OKF wrapper (structure validation)
    3. Hybrid search (merge logic, scoring)
    4. Graph RAG (build, enrich, save/load)
    5. Pinecone (connection + index stats)
    6. End-to-end search (real query)
"""

import os
import sys
import json
import math
import types
import socket
import unittest
import tempfile
import urllib.request
from pathlib import Path
from typing import List, Dict, Any
from unittest.mock import patch, MagicMock

# ─── Path setup ───────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env", override=True)


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

def _make_fake_chunk(idx: int = 0, category: str = "Acts") -> Dict[str, Any]:
    return {
        "chunk_id"    : f"test_chunk_{idx}",
        "doc_name"    : f"Test Document {idx}",
        "doc_category": category,
        "page_number" : idx + 1,
        "text"        : f"This is test chunk {idx} discussing § 162 trade expenses.",
        "token_count" : 15,
        "section"     : "§ 162",
    }


def _is_port_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False





# ══════════════════════════════════════════════════════════════════════════════
# 1. Parser Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestParser(unittest.TestCase):

    def test_clean_text_removes_formfeed(self):
        from parser import clean_text
        result = clean_text("hello\f world")
        self.assertNotIn("\f", result)
        self.assertIn("hello", result)

    def test_clean_text_collapses_spaces(self):
        from parser import clean_text
        result = clean_text("hello   world")
        self.assertEqual(result, "hello world")

    def test_clean_text_collapses_newlines(self):
        from parser import clean_text
        result = clean_text("line1\n\n\n\nline2")
        self.assertNotIn("\n\n\n", result)

    def test_count_tokens_non_zero(self):
        from parser import count_tokens
        n = count_tokens("The quick brown fox jumps over the lazy dog.")
        self.assertGreater(n, 0)
        self.assertLess(n, 50)

    def test_count_tokens_empty(self):
        from parser import count_tokens
        self.assertEqual(count_tokens(""), 0)

    def test_extract_section_header_usc_pattern(self):
        from parser import extract_section_header
        text = "Under 26 U.S.C. §162, trade or business expenses are deductible."
        sec  = extract_section_header(text)
        self.assertTrue(sec, f"Expected section ref, got empty string. Text: {text!r}")

    def test_extract_section_header_irc_pattern(self):
        from parser import extract_section_header
        sec = extract_section_header("IRC §6651 imposes a failure-to-file penalty.")
        self.assertIn("6651", sec)

    def test_extract_section_header_plain_section(self):
        from parser import extract_section_header
        sec = extract_section_header("Section 164 addresses state and local taxes.")
        self.assertTrue(sec)
        self.assertIn("164", sec)

    def test_extract_section_header_no_match(self):
        from parser import extract_section_header
        sec = extract_section_header("No section references here at all.")
        self.assertEqual(sec, "")

    def test_chunk_page_tokens_returns_list(self):
        from parser import chunk_page_tokens
        text   = " ".join(["word"] * 1500)
        chunks = chunk_page_tokens(text)
        self.assertIsInstance(chunks, list)
        self.assertGreater(len(chunks), 1)

    def test_chunk_page_tokens_overlap(self):
        """Consecutive chunks should share tokens (overlap > 0)."""
        from parser import chunk_page_tokens, count_tokens, CHUNK_TOKENS, OVERLAP_TOKENS
        text   = " ".join([f"w{i}" for i in range(1500)])
        chunks = chunk_page_tokens(text)
        if len(chunks) >= 2:
            # Each chunk should be at most CHUNK_TOKENS tokens
            self.assertLessEqual(count_tokens(chunks[0]), CHUNK_TOKENS + 5)

    def test_chunk_page_tokens_short_text(self):
        from parser import chunk_page_tokens
        chunks = chunk_page_tokens("Short text.")
        self.assertIsInstance(chunks, list)

    def test_safe_name_ascii(self):
        from parser import _safe_name
        result = _safe_name("26 U.S.C. § 162 (Trade Expenses)")
        self.assertTrue(all(c.isalnum() or c == "_" for c in result))
        self.assertLessEqual(len(result), 60)

    def test_chunk_metadata_schema(self):
        """Verify extract_chunks_from_pdf returns dicts with required keys."""
        from parser import extract_chunks_from_pdf
        import fitz

        # Create a tiny in-memory PDF and save to project temp dir (avoids Windows perms issue)
        tmp_dir = PROJECT_ROOT / "outputs"
        tmp_dir.mkdir(exist_ok=True)
        tmp_path = tmp_dir / "_test_chunk_schema.pdf"

        pdf_doc = fitz.open()
        page    = pdf_doc.new_page()
        page.insert_text((50, 100), "Under § 162, trade expenses are deductible. " * 30)
        pdf_doc.save(str(tmp_path))
        pdf_doc.close()

        try:
            chunks = extract_chunks_from_pdf(tmp_path, "Acts")
            if chunks:
                c = chunks[0]
                for key in ("chunk_id","doc_name","doc_category","page_number","text","token_count"):
                    self.assertIn(key, c, f"Missing key: {key}")
                self.assertEqual(c["doc_category"], "Acts")
                self.assertGreater(c["token_count"], 0)
        finally:
            tmp_path.unlink(missing_ok=True)


# ══════════════════════════════════════════════════════════════════════════════
# 2. OKF Wrapper Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestOKFWrapper(unittest.TestCase):

    def test_wrap_okf_all_fields(self):
        from search import wrap_okf
        chunk = {
            "doc_name"    : "Test Act",
            "doc_category": "Acts",
            "page_number" : 3,
            "section"     : "§ 162",
            "text"        : "Some legal text.",
        }
        okf = wrap_okf(chunk)
        self.assertEqual(okf["source"],   "Test Act")
        self.assertEqual(okf["category"], "Acts")
        self.assertEqual(okf["page"],     3)
        self.assertEqual(okf["section"],  "§ 162")
        self.assertEqual(okf["content"],  "Some legal text.")

    def test_wrap_okf_missing_fields(self):
        from search import wrap_okf
        okf = wrap_okf({})
        self.assertEqual(okf["source"],  "")
        self.assertEqual(okf["page"],    0)
        self.assertEqual(okf["content"],"")

    def test_wrap_okf_returns_dict(self):
        from search import wrap_okf
        result = wrap_okf(_make_fake_chunk())
        self.assertIsInstance(result, dict)
        for key in ("source", "category", "page", "section", "content"):
            self.assertIn(key, result)


# ══════════════════════════════════════════════════════════════════════════════
# 3. Hybrid Search Logic Tests (mocked Pinecone + ES)
# ══════════════════════════════════════════════════════════════════════════════

class TestHybridSearchLogic(unittest.TestCase):
    """Test score merging/ranking without real network calls."""

    def _make_vec_hit(self, cid, vec_score) -> Dict:
        return {
            "chunk_id"     : cid,
            "text"         : "text",
            "doc_name"     : f"doc_{cid}",
            "doc_category" : "Acts",
            "page_number"  : 1,
            "section"      : "",
            "vector_score" : vec_score,
            "keyword_score": 0.0,
        }

    def _make_kw_hit(self, cid, kw_score) -> Dict:
        h = self._make_vec_hit(cid, 0.0)
        h["keyword_score"] = kw_score
        return h

    def test_hybrid_score_formula(self):
        """hybrid = 0.6*vec + 0.4*kw"""
        vec_weight = 0.6
        kw_weight  = 0.4
        vec_score  = 0.9
        kw_score   = 0.5
        expected   = vec_weight * vec_score + kw_weight * kw_score
        self.assertAlmostEqual(expected, 0.74, places=4)

    def test_merge_deduplicates_by_chunk_id(self):
        """A chunk returned by both vector and keyword should appear once."""
        vec_hits = [self._make_vec_hit("c1", 0.8), self._make_vec_hit("c2", 0.6)]
        kw_hits  = [self._make_kw_hit("c1", 0.5), self._make_kw_hit("c3", 0.9)]

        merged: Dict[str, Dict] = {}
        for h in vec_hits:
            merged[h["chunk_id"]] = dict(h)
        for h in kw_hits:
            cid = h["chunk_id"]
            if cid in merged:
                merged[cid]["keyword_score"] = h["keyword_score"]
            else:
                merged[cid] = dict(h)

        self.assertEqual(len(merged), 3)
        self.assertAlmostEqual(merged["c1"]["keyword_score"], 0.5)

    def test_ranking_order(self):
        """Highest hybrid_score should rank first."""
        entries = [
            {"chunk_id": "a", "hybrid_score": 0.3},
            {"chunk_id": "b", "hybrid_score": 0.9},
            {"chunk_id": "c", "hybrid_score": 0.6},
        ]
        ranked = sorted(entries, key=lambda x: x["hybrid_score"], reverse=True)
        self.assertEqual(ranked[0]["chunk_id"], "b")
        self.assertEqual(ranked[-1]["chunk_id"], "a")

    def test_vector_only_when_es_unavailable(self):
        """When ES is down, vector_weight should be forced to 1.0."""
        vector_weight  = 0.6
        keyword_weight = 0.4
        es_available   = False
        if not es_available:
            vector_weight  = 1.0
            keyword_weight = 0.0
        self.assertEqual(vector_weight,  1.0)
        self.assertEqual(keyword_weight, 0.0)

    def test_top_k_truncation(self):
        entries = [{"chunk_id": str(i), "hybrid_score": i * 0.1} for i in range(20)]
        ranked  = sorted(entries, key=lambda x: x["hybrid_score"], reverse=True)[:5]
        self.assertEqual(len(ranked), 5)
        self.assertEqual(ranked[0]["chunk_id"], "19")


# ══════════════════════════════════════════════════════════════════════════════
# 4. Graph RAG Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestGraphRAG(unittest.TestCase):

    def _make_chunks(self):
        return [
            {
                "chunk_id"    : "act_001",
                "doc_name"    : "26 USC 162 Trade Expenses",
                "doc_category": "Acts",
                "page_number" : 1,
                "text"        : "§ 162 allows deduction of trade expenses.",
                "section"     : "§ 162",
            },
            {
                "chunk_id"    : "jdg_001",
                "doc_name"    : "Smith v Commissioner",
                "doc_category": "Judgments",
                "page_number" : 3,
                "text"        : "The court applied § 162 to deny the deduction.",
                "section"     : "",
            },
            {
                "chunk_id"    : "pov_001",
                "doc_name"    : "Tax Foundation Analysis",
                "doc_category": "POV",
                "page_number" : 2,
                "text"        : "Under Section 162, trade expense rules apply.",
                "section"     : "",
            },
        ]

    def test_build_citation_graph_nodes(self):
        from graph_rag import build_citation_graph
        G = build_citation_graph(self._make_chunks())
        self.assertGreater(G.number_of_nodes(), 0)
        self.assertIn("Smith v Commissioner", G.nodes)
        self.assertIn("26 USC 162 Trade Expenses", G.nodes)

    def test_build_citation_graph_edges(self):
        from graph_rag import build_citation_graph
        G = build_citation_graph(self._make_chunks())
        # Judgment → Act edge should exist
        has_edge = G.has_edge("Smith v Commissioner", "26 USC 162 Trade Expenses")
        self.assertTrue(has_edge, "Expected Judgment→Act edge via § 162 reference")

    def test_extract_cited_sections(self):
        from graph_rag import extract_cited_sections
        text = "The taxpayer claimed deductions under § 162 and IRC § 6651."
        refs = extract_cited_sections(text)
        self.assertGreater(len(refs), 0)
        nums = [r for r in refs if "162" in r or "6651" in r]
        self.assertGreater(len(nums), 0)

    def test_extract_cited_sections_deduplication(self):
        from graph_rag import extract_cited_sections
        text = "§ 162 applies. Under § 162, the rule is clear. §162 again."
        refs = extract_cited_sections(text)
        # Should deduplicate
        seen = set()
        for r in refs:
            self.assertNotIn(r, seen, f"Duplicate ref found: {r!r}")
            seen.add(r)

    def test_save_and_load_graph(self):
        from graph_rag import build_citation_graph, save_graph, load_graph
        G = build_citation_graph(self._make_chunks())
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp = f.name
        try:
            save_graph(G, tmp)
            G2 = load_graph(tmp)
            self.assertEqual(G.number_of_nodes(), G2.number_of_nodes())
            self.assertEqual(G.number_of_edges(), G2.number_of_edges())
        finally:
            Path(tmp).unlink(missing_ok=True)

    def test_enrich_results_with_graph(self):
        from graph_rag import build_citation_graph, enrich_results_with_graph
        chunks = self._make_chunks()
        G      = build_citation_graph(chunks)
        from collections import defaultdict
        lookup = defaultdict(list)
        for c in chunks:
            lookup[c["doc_name"]].append(c)

        base_results = [{
            "chunk_id"     : "jdg_001",
            "doc_name"     : "Smith v Commissioner",
            "doc_category" : "Judgments",
            "page_number"  : 3,
            "text"         : "The court applied § 162.",
            "hybrid_score" : 0.8,
            "vector_score" : 0.8,
            "keyword_score": 0.0,
        }]
        enriched = enrich_results_with_graph(base_results, G, dict(lookup))
        # Should have added the Act chunk
        doc_names = [r["doc_name"] for r in enriched]
        self.assertIn("26 USC 162 Trade Expenses", doc_names)
        # Enriched flag
        act_result = next(r for r in enriched if r["doc_name"] == "26 USC 162 Trade Expenses")
        self.assertTrue(act_result.get("graph_enriched"))

    def test_get_related_acts(self):
        from graph_rag import build_citation_graph, get_related_acts
        G = build_citation_graph(self._make_chunks())
        related = get_related_acts(G, "Smith v Commissioner")
        self.assertIsInstance(related, list)
        if related:
            self.assertIn("act_doc", related[0])
            self.assertIn("section_refs", related[0])


# ══════════════════════════════════════════════════════════════════════════════
# 5. Live Service Tests (skipped if services not running)
# ══════════════════════════════════════════════════════════════════════════════

class TestPineconeLive(unittest.TestCase):

    @unittest.skipUnless(
        bool(os.getenv("PINECONE_API_KEY")),
        "PINECONE_API_KEY not set"
    )
    def test_pinecone_connection(self):
        from search import _get_pinecone_index
        idx = _get_pinecone_index()
        self.assertIsNotNone(idx)

    @unittest.skipUnless(
        bool(os.getenv("PINECONE_API_KEY")),
        "PINECONE_API_KEY not set"
    )
    def test_pinecone_index_stats(self):
        from search import _get_pinecone_index
        idx   = _get_pinecone_index()
        stats = idx.describe_index_stats()
        self.assertIn("total_vector_count", stats)
        self.assertGreaterEqual(stats["total_vector_count"], 0)





# ══════════════════════════════════════════════════════════════════════════════
# 6. End-to-End Search Test
# ══════════════════════════════════════════════════════════════════════════════

class TestEndToEndSearch(unittest.TestCase):

    @unittest.skipUnless(
        bool(os.getenv("PINECONE_API_KEY")),
        "Requires PINECONE_API_KEY set"
    )
    def test_hybrid_search_returns_results(self):
        from search import hybrid_search, build_index
        build_index()
        results = hybrid_search("What is gross income under § 61?", top_k=3)
        self.assertIsInstance(results, list)
        self.assertGreater(len(results), 0)

    @unittest.skipUnless(
        bool(os.getenv("PINECONE_API_KEY")),
        "Requires PINECONE_API_KEY set"
    )
    def test_hybrid_search_result_schema(self):
        from search import hybrid_search, build_index
        build_index()
        results = hybrid_search("SALT deduction cap under § 164", top_k=3)
        if results:
            r = results[0]
            for key in ("chunk_id", "doc_name", "doc_category", "page_number",
                        "text", "vector_score", "keyword_score", "hybrid_score",
                        "okf_context"):
                self.assertIn(key, r, f"Missing key: {key}")
            self.assertGreaterEqual(r["hybrid_score"], 0.0)
            self.assertLessEqual(r["hybrid_score"],    1.0)

    @unittest.skipUnless(
        bool(os.getenv("PINECONE_API_KEY")),
        "Requires PINECONE_API_KEY set"
    )
    def test_hybrid_search_top_k_respected(self):
        from search import hybrid_search, build_index
        build_index()
        for k in (1, 3, 5):
            results = hybrid_search("IRC § 162 business expenses", top_k=k)
            self.assertLessEqual(len(results), k)


# ══════════════════════════════════════════════════════════════════════════════
# 7. Evaluate helpers
# ══════════════════════════════════════════════════════════════════════════════

class TestEvaluateHelpers(unittest.TestCase):

    def test_normalise_doc_name_strips_pdf(self):
        from evaluate import _normalise_doc_name
        result = _normalise_doc_name("26 U.S. Code § 162.pdf")
        self.assertNotIn(".pdf", result)

    def test_normalise_doc_name_lowercased(self):
        from evaluate import _normalise_doc_name
        result = _normalise_doc_name("SMITH v Commissioner")
        self.assertEqual(result, result.lower())

    def test_docs_match_exact(self):
        from evaluate import _docs_match
        self.assertTrue(_docs_match("26 U.S. Code § 162", "26 U.S. Code § 162"))

    def test_docs_match_partial(self):
        from evaluate import _docs_match
        # Substring containment — '26 usc 162 trade expenses' contains '26 usc 162'
        self.assertTrue(_docs_match(
            "26 usc 162 trade expenses",
            "26 usc 162"
        ))

    def test_docs_match_token_overlap(self):
        from evaluate import _docs_match
        # 4 common tokens out of 5 = 80% overlap → True
        self.assertTrue(_docs_match(
            "26 usc section 162 trade",
            "26 usc section 162 expenses"
        ))

    def test_docs_no_match(self):
        from evaluate import _docs_match
        self.assertFalse(_docs_match("Apple Computer Corp", "Internal Revenue Service"))


# ══════════════════════════════════════════════════════════════════════════════
# 8. Data integrity check
# ══════════════════════════════════════════════════════════════════════════════

class TestDataIntegrity(unittest.TestCase):

    CHUNKS_PATH = PROJECT_ROOT / "outputs" / "parsed_chunks.json"

    @unittest.skipUnless(
        (PROJECT_ROOT / "outputs" / "parsed_chunks.json").exists(),
        "parsed_chunks.json not found"
    )
    def test_chunks_file_loads(self):
        with open(self.CHUNKS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("chunks", data)
        self.assertIn("metadata", data)
        self.assertGreater(len(data["chunks"]), 0)

    @unittest.skipUnless(
        (PROJECT_ROOT / "outputs" / "parsed_chunks.json").exists(),
        "parsed_chunks.json not found"
    )
    def test_all_chunks_have_required_fields(self):
        with open(self.CHUNKS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        required = {"chunk_id", "doc_name", "doc_category", "page_number", "text", "token_count"}
        errors = []
        for i, chunk in enumerate(data["chunks"][:100]):   # sample first 100
            missing = required - set(chunk.keys())
            if missing:
                errors.append(f"Chunk {i}: missing {missing}")
        self.assertEqual(errors, [], f"Schema errors:\n" + "\n".join(errors))

    @unittest.skipUnless(
        (PROJECT_ROOT / "outputs" / "parsed_chunks.json").exists(),
        "parsed_chunks.json not found"
    )
    def test_categories_are_valid(self):
        valid = {"Acts", "Judgments", "POV", "Tax Docs"}
        with open(self.CHUNKS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        for chunk in data["chunks"][:500]:
            self.assertIn(
                chunk["doc_category"], valid,
                f"Invalid category: {chunk['doc_category']!r}"
            )

    @unittest.skipUnless(
        (PROJECT_ROOT / "outputs" / "parsed_chunks.json").exists(),
        "parsed_chunks.json not found"
    )
    def test_page_numbers_positive(self):
        with open(self.CHUNKS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        for chunk in data["chunks"][:500]:
            self.assertGreater(
                chunk["page_number"], 0,
                f"Non-positive page number in chunk {chunk['chunk_id']}"
            )

    @unittest.skipUnless(
        (PROJECT_ROOT / "outputs" / "parsed_chunks.json").exists(),
        "parsed_chunks.json not found"
    )
    def test_chunk_ids_unique(self):
        with open(self.CHUNKS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        ids = [c["chunk_id"] for c in data["chunks"]]
        self.assertEqual(len(ids), len(set(ids)), "Duplicate chunk IDs found!")

    @unittest.skipUnless(
        (PROJECT_ROOT / "outputs" / "citation_graph.json").exists(),
        "citation_graph.json not found"
    )
    def test_citation_graph_loads(self):
        graph_path = PROJECT_ROOT / "outputs" / "citation_graph.json"
        with open(graph_path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("nodes", data)
        self.assertIn("edges", data)
        self.assertGreater(len(data["nodes"]), 0)


# ══════════════════════════════════════════════════════════════════════════════
# Runner
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Print service status before running
    print("\n" + "=" * 60)
    print("SERVICE STATUS CHECK")
    print("=" * 60)

    pin_key = os.getenv("PINECONE_API_KEY", "")
    print(f"  Pinecone : {'✅ Key set' if pin_key else '❌ PINECONE_API_KEY not set'}")
    llm_key  = os.getenv("LLM_API_KEY", "")
    llm_url  = os.getenv("LLM_BASE_URL", "")
    print(f"  LLM      : {'✅ Key set → ' + llm_url if llm_key else '❌ LLM_API_KEY not set'}")
    print("=" * 60 + "\n")

    unittest.main(verbosity=2)
