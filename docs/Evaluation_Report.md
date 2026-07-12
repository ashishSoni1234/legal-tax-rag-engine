# Legal & Tax RAG System — Evaluation Report

*Generated: 2026-07-12 17:30:04*

---

## Overall Performance

| Metric | Value |
|--------|-------|
| Total queries evaluated | 90 |
| **Retrieval Top-1 Accuracy** | **83.3%** |
| **Retrieval Top-3 Accuracy** | **88.9%** |
| **Retrieval Top-5 Accuracy** | **93.3%** |
| Average Faithfulness (1–5) | 3.89 |

> [!NOTE]
> Faithfulness is scored 1–5 by Claude (LLM-as-judge). 5 = fully faithful, 1 = hallucinated.

---

## Breakdown by Category

| Category | N | Top-1 Acc | Top-3 Acc | Avg Faith |
|----------|---|-----------|-----------|-----------|
| Acts | 40 | 97.5% | 97.5% | 3.89 |
| Cross-Doc | 10 | 20.0% | 30.0% | N/A |
| Judgments | 25 | 100.0% | 100.0% | N/A |
| POV | 8 | 75.0% | 87.5% | N/A |
| Tax Docs | 7 | 42.9% | 85.7% | N/A |

---

## Breakdown by Difficulty

| Difficulty | N | Top-1 Acc | Top-3 Acc | Avg Faith |
|------------|---|-----------|-----------|-----------|
| Easy | 7 | 85.7% | 100.0% | 4.00 |
| Hard | 45 | 82.2% | 84.4% | 4.00 |
| Medium | 38 | 84.2% | 92.1% | 3.73 |

---

## Worst 10 Performing Queries (for manual review)

| ID | Category | Difficulty | Faithfulness | Top-1 | Query |
|----|----------|------------|--------------|-------|-------|
| A001 | Acts | Medium | 1 | ✓ | What is the top individual income tax rate imposed under 26 U.S.C. §1 for taxabl |
| A019 | Acts | Hard | 4 | ✗ | What is the SALT deduction cap under 26 U.S.C. §164(b)(6) for tax years 2025 and |
| A002 | Acts | Hard | 4 | ✓ | Under 26 U.S.C. §1, how is the cost-of-living adjustment to the tax rate bracket |
| A003 | Acts | Hard | 4 | ✓ | What is the 'kiddie tax' rule under 26 U.S.C. §1, and at what age does it genera |
| A004 | Acts | Easy | 4 | ✓ | How does 26 U.S.C. §61(a) define gross income, and what are three items explicit |
| A005 | Acts | Easy | 4 | ✓ | According to 26 U.S.C. §61, where in the Code can taxpayers find items specifica |
| A006 | Acts | Medium | 4 | ✓ | Under 26 U.S.C. §62, what is the definition of 'adjusted gross income' for an in |
| A007 | Acts | Hard | 4 | ✓ | Under 26 U.S.C. §62(b), what are the requirements for a 'qualified performing ar |
| A008 | Acts | Medium | 4 | ✓ | Under 26 U.S.C. §101(a), are life insurance proceeds paid by reason of death gen |
| A009 | Acts | Hard | 4 | ✓ | How does 26 U.S.C. §101(g) treat accelerated death benefits paid to a terminally |

---

## Notes
- Retrieval accuracy measures whether the ground truth source document appears in the system's retrieved results.
- Fuzzy matching (60% token overlap) is used for document name comparison.
- Graph RAG enrichment adds Act documents cited by retrieved Judgment chunks.
- Faithfulness is evaluated by Llama 3.1 via OpenAI client at temperature=0.

*End of Report*