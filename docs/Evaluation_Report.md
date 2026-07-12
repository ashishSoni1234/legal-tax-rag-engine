# Legal & Tax RAG System — Evaluation Report

*Generated: 2026-07-12 21:15:16*

---

## Overall Performance

| Metric | Value |
|--------|-------|
| Total queries evaluated | 2 |
| **Retrieval Top-1 Accuracy** | **100.0%** |
| **Retrieval Top-3 Accuracy** | **100.0%** |
| **Retrieval Top-5 Accuracy** | **100.0%** |
| Average Faithfulness (1–5) | 2.5 |

> [!NOTE]
> Faithfulness is scored 1–5 by Claude (LLM-as-judge). 5 = fully faithful, 1 = hallucinated.

---

## Breakdown by Category

| Category | N | Top-1 Acc | Top-3 Acc | Avg Faith |
|----------|---|-----------|-----------|-----------|
| Acts | 2 | 100.0% | 100.0% | 2.50 |

---

## Breakdown by Difficulty

| Difficulty | N | Top-1 Acc | Top-3 Acc | Avg Faith |
|------------|---|-----------|-----------|-----------|
| Hard | 1 | 100.0% | 100.0% | 4.00 |
| Medium | 1 | 100.0% | 100.0% | 1.00 |

---

## Worst 10 Performing Queries (for manual review)

| ID | Category | Difficulty | Faithfulness | Top-1 | Query |
|----|----------|------------|--------------|-------|-------|
| A001 | Acts | Medium | 1 | ✓ | What is the top individual income tax rate imposed under 26 U.S.C. §1 for taxabl |
| A002 | Acts | Hard | 4 | ✓ | Under 26 U.S.C. §1, how is the cost-of-living adjustment to the tax rate bracket |

---

## Notes
- Retrieval accuracy measures whether the ground truth source document appears in the system's retrieved results.
- Fuzzy matching (60% token overlap) is used for document name comparison.
- Graph RAG enrichment adds Act documents cited by retrieved Judgment chunks.
- Faithfulness is evaluated by Llama 3.1 via OpenAI client at temperature=0.

*End of Report*