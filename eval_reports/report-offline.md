# RAG Evaluation Report

- **Profile**: `offline`
- **Generator**: `extractive:extractive-v1`
- **Embedder**: `hashing:hashing-512:512`
- **Reranker**: `lexical`
- **Prompt**: `answer.v2` (hash `82d4affe607d`)
- **Golden set**: data/golden/golden_set.jsonl (105 questions)
- **Run at**: 2026-08-18T17:54:26+00:00  ·  **Duration**: 1.37s

## Gate: PASS

| Metric | Value | Bound | Result |
|---|---|---|---|
| `retrieval.hit_at_1` | 0.9140 | min=0.86 | pass |
| `retrieval.recall_at_k` | 0.9892 | min=0.95 | pass |
| `retrieval.mrr` | 0.9498 | min=0.9 | pass |
| `answer_quality.faithfulness` | 1.0000 | min=0.9 | pass |
| `answer_quality.answer_f1` | 0.4012 | min=0.36 | pass |
| `answer_quality.citation_precision` | 0.7715 | min=0.72 | pass |
| `answer_quality.correct_source_cited_rate` | 0.9101 | min=0.86 | pass |
| `answer_quality.answer_citation_rate` | 1.0000 | min=1.0 | pass |
| `abstention.abstention_recall` | 1.0000 | min=0.9 | pass |
| `abstention.abstention_precision` | 0.7500 | min=0.65 | pass |
| `abstention.false_abstention_rate` | 0.0430 | max=0.1 | pass |
| `operational.latency_p95_ms` | 7.3500 | max=2000 | pass |

## Retrieval

| Metric | Value |
|---|---|
| `hit_at_1` | 0.9140 |
| `recall_at_k` | 0.9892 |
| `mrr` | 0.9498 |

## Answer Quality

| Metric | Value |
|---|---|
| `faithfulness` | 1.0000 |
| `answer_f1` | 0.4012 |
| `citation_precision` | 0.7715 |
| `correct_source_cited_rate` | 0.9101 |
| `answer_citation_rate` | 1.0000 |

## Abstention

| Metric | Value |
|---|---|
| `abstention_recall` | 1.0000 |
| `abstention_precision` | 0.7500 |
| `false_abstention_rate` | 0.0430 |

## Operational

| Metric | Value |
|---|---|
| `latency_p50_ms` | 5.6000 |
| `latency_p95_ms` | 7.3500 |
| `mean_input_tokens` | 610 |
| `mean_output_tokens` | 19 |

## Abstention reasons

| Reason | Count |
|---|---|
| `question_not_covered` | 13 |
| `low_relevance` | 3 |

## By difficulty

| Difficulty | n | recall@k | faithfulness | answer F1 | abstained |
|---|---|---|---|---|---|
| abstention | 12 | 0.000 | 0.000 | 0.000 | 12 |
| easy | 37 | 0.973 | 1.000 | 0.323 | 1 |
| hard | 12 | 1.000 | 1.000 | 0.380 | 1 |
| medium | 44 | 1.000 | 1.000 | 0.474 | 2 |

## Notable failures (5)

| id | question | issue |
|---|---|---|
| `q007` | How are amounts expressed in the API? | false abstention (question_not_covered) |
| `q029` | What error is returned when refunding a payment that is not succeeded? | correct document not retrieved |
| `q036` | Is it cheaper to refund a customer or to fight a dispute and lose? | false abstention (question_not_covered) |
| `q062` | Is the CVC ever stored? | false abstention (question_not_covered) |
| `q090` | What qualifies as a Severity 1 issue? | false abstention (question_not_covered) |
