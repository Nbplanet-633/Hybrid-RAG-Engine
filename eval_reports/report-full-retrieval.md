# RAG Evaluation Report

- **Profile**: `full-retrieval`
- **Generator**: `extractive:extractive-v1`
- **Embedder**: `sentence-transformers:sentence-transformers/all-MiniLM-L6-v2:384`
- **Reranker**: `cross-encoder`
- **Prompt**: `answer.v2` (hash `82d4affe607d`)
- **Golden set**: data/golden/golden_set.jsonl (105 questions)
- **Run at**: 2026-08-18T17:36:47+00:00  ·  **Duration**: 9.26s

## Gate: PASS

| Metric | Value | Bound | Result |
|---|---|---|---|
| `retrieval.hit_at_1` | 0.9032 | min=0.85 | pass |
| `retrieval.recall_at_k` | 0.9892 | min=0.95 | pass |
| `retrieval.mrr` | 0.9409 | min=0.9 | pass |
| `answer_quality.faithfulness` | 1.0000 | min=0.9 | pass |
| `answer_quality.answer_f1` | 0.4094 | min=0.36 | pass |
| `answer_quality.citation_precision` | 0.8015 | min=0.75 | pass |
| `answer_quality.correct_source_cited_rate` | 0.9213 | min=0.88 | pass |
| `answer_quality.answer_citation_rate` | 1.0000 | min=1.0 | pass |
| `abstention.abstention_recall` | 1.0000 | min=0.9 | pass |
| `abstention.abstention_precision` | 0.7500 | min=0.65 | pass |
| `abstention.false_abstention_rate` | 0.0430 | max=0.1 | pass |
| `operational.latency_p95_ms` | 7.4900 | max=5000 | pass |

## Retrieval

| Metric | Value |
|---|---|
| `hit_at_1` | 0.9032 |
| `recall_at_k` | 0.9892 |
| `mrr` | 0.9409 |

## Answer Quality

| Metric | Value |
|---|---|
| `faithfulness` | 1.0000 |
| `answer_f1` | 0.4094 |
| `citation_precision` | 0.8015 |
| `correct_source_cited_rate` | 0.9213 |
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
| `latency_p50_ms` | 5.8000 |
| `latency_p95_ms` | 7.4900 |
| `mean_input_tokens` | 639 |
| `mean_output_tokens` | 18 |

## Abstention reasons

| Reason | Count |
|---|---|
| `question_not_covered` | 14 |
| `low_relevance` | 2 |

## By difficulty

| Difficulty | n | recall@k | faithfulness | answer F1 | abstained |
|---|---|---|---|---|---|
| abstention | 12 | 0.000 | 0.000 | 0.000 | 12 |
| easy | 37 | 0.973 | 1.000 | 0.338 | 1 |
| hard | 12 | 1.000 | 1.000 | 0.395 | 1 |
| medium | 44 | 1.000 | 1.000 | 0.474 | 2 |

## Notable failures (5)

| id | question | issue |
|---|---|---|
| `q007` | How are amounts expressed in the API? | false abstention (question_not_covered) |
| `q036` | Is it cheaper to refund a customer or to fight a dispute and lose? | false abstention (question_not_covered) |
| `q062` | Is the CVC ever stored? | false abstention (question_not_covered) |
| `q068` | What is the maximum bug bounty payout? | correct document not retrieved |
| `q090` | What qualifies as a Severity 1 issue? | false abstention (question_not_covered) |
