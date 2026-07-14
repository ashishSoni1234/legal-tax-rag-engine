# Legal & Tax RAG System — Evaluation Report

*Generated: 2026-07-12 21:15:16*

---

## Overall Performance

| Metric | Value |
|--------|-------|
| Total queries evaluated | 90 |
| **Retrieval Top-1 Accuracy** | **83.3%** |
| **Retrieval Top-3 Accuracy** | **88.9%** |
| **Retrieval Top-5 Accuracy** | **93.3%** |
| Average Faithfulness (LLM-as-Judge) | 3.89 / 5 |
| Judgments Category Top-1 Accuracy | 100.0% |

> [!NOTE]
> Faithfulness is scored 1–5 by Claude (LLM-as-judge). 5 = fully faithful, 1 = hallucinated.

---

## Notes
- Retrieval accuracy measures whether the ground truth source document appears in the system's retrieved results.
- Fuzzy matching (60% token overlap) is used for document name comparison.
- Graph RAG enrichment adds Act documents cited by retrieved Judgment chunks.
- Faithfulness is evaluated by Llama 3.1 via OpenAI client at temperature=0.

*End of Report*