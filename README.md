# Ask My Docs

A domain-specific Retrieval-Augmented Generation system that **refuses to answer when the
evidence does not support one**.

Hybrid retrieval (dense + BM25 fused with Reciprocal Rank Fusion) → cross-encoder reranking →
citation-enforced generation, with a 105-question golden dataset wired into CI as a regression
gate. Every returned answer carries a resolvable source citation, or the system abstains and
says why.

**Upload your own PDF, Markdown, text, or HTML files** in the web UI or over the API and ask
questions about them. See [Ask your own documents](#ask-your-own-documents).

```
                    ┌──────────────────────────────────────────────┐
   documents ──────▶│ INGEST                                       │
  (md/pdf/html)     │  loaders → structural chunker (500-800 tok,   │
                    │  100 overlap) → embeddings → vector store    │
                    │                            └──▶ BM25 index   │
                    └──────────────────────────────────────────────┘
                                        │
   question ────────────────────────────┼──────────────────────────────────┐
                                        ▼                                  │
        ┌───────────────────┐   ┌────────────────┐                         │
        │ dense retrieval   │   │ BM25 retrieval │   both legs, top-20     │
        └─────────┬─────────┘   └───────┬────────┘                         │
                  └────────┬────────────┘                                 │
                           ▼                                              │
                  Reciprocal Rank Fusion (k=60)  ──▶ 24 candidates        │
                           ▼                                              │
                  cross-encoder rerank           ──▶ top 5               │
                           ▼                                              │
        ┌──────────────────────────────────────────────────────┐          │
        │ CITATION-ENFORCED GENERATION                         │          │
        │  gate 1  relevance floor          ─┐                 │          │
        │  gate 2  question-coverage (IDF)   ├─ abstain before │          │
        │  gate 3  refusal sentinel          │   or after the  │◀─────────┘
        │  gate 4  citation resolution       │   model call    │
        │  gate 5  grounding check          ─┘                 │
        └──────────────────────────────────────────────────────┘
                           ▼
              Answer + citations   |   or   Abstention + reason
```

---

## Measured results

105 golden questions · 93 answerable across 16 categories · 12 deliberately unanswerable.
Full report: [`eval_reports/report-offline.md`](eval_reports/report-offline.md).

Two profiles were measured. `full-retrieval` uses real models — `all-MiniLM-L6-v2`
embeddings, ChromaDB, and the `ms-marco-MiniLM-L-6-v2` cross-encoder — paired with the
deterministic generator, which isolates retrieval quality from generation quality.

| Family | Metric | `offline` | `full-retrieval` |
|---|---|---|---|
| **Retrieval** | recall@5 | 0.989 | 0.989 |
| | MRR | **0.950** | 0.941 |
| | hit@1 | **0.914** | 0.903 |
| **Citations** | answers carrying a resolvable citation | 1.000 | 1.000 |
| | cited an expected source | 0.910 | **0.921** |
| | citation precision | 0.771 | **0.801** |
| **Abstention** | unanswerable questions refused | 1.000 | 1.000 |
| | false-abstention rate | 0.043 | 0.043 |
| | abstention precision | 0.750 | 0.750 |
| **Answer** | token-F1 vs reference | 0.401 | **0.409** |
| **Cost** | p95 latency | 7.3 ms | 6.1 ms |
| | eval wall-clock | 1.4 s | 9.3 s |

Real models improve what they should — citation precision +0.030, correct-source rate
+0.011, answer F1 +0.008 — while hit@1 and MRR shift slightly the other way. Recall@5 is
identical at 0.989, so the reranker sees the right document either way; the cross-encoder
just orders the top few differently. On a 23-chunk corpus that difference is within noise,
and the honest read is that **hashed features plus BM25 are competitive at this scale** —
the cross-encoder's advantage would be expected to widen on a corpus large enough for
lexical overlap to stop being discriminative.

`257 tests · 86% coverage · 12/12 thresholds met on both measured profiles`

> **What is measured and what is not.** Both columns above are real runs. What has *not*
> been exercised is **Claude generation** — the `full` profile's `claude-opus-5` path. Its
> embedder, reranker, vector store, and client all construct and run; only the API call is
> unverified, because no `ANTHROPIC_API_KEY` was available. In `eval/thresholds.yaml`, the
> `full` profile's retrieval and citation bounds are inherited from the measured
> `full-retrieval` run (identical embedder, store, and reranker), while its faithfulness,
> answer-F1, and latency bounds remain marked as estimates.
>
> Two figures deserve an asterisk in both columns: **faithfulness reads 1.000 trivially**,
> because the extractive generator quotes source sentences verbatim — it becomes a real
> metric only on the Claude path. And **token-F1 ~0.41 is a floor, not a ceiling**: extracted
> sentences are scored against hand-written paraphrases, so a perfectly correct extraction
> still lands near 0.4.

### Where it fails

Listed because a system whose failures you cannot name is a system you cannot trust.

- **4 false abstentions** (`q007`, `q036`, `q062`, `q090`), all from the question-coverage
  gate. Each asks about a concept the corpus covers using vocabulary the corpus does not — "How
  are amounts *expressed*?" against a document that says "amounts are integers in the minor
  unit". This is the cost of a lexical out-of-scope gate, and it is the tradeoff the threshold
  is tuned on: 0.60 buys 12/12 abstention recall for 4 false positives.
- **1 retrieval miss** (`q029`) — the only question whose correct document never entered the
  top 5.
- **Abstention precision 0.750** follows arithmetically: 16 abstentions, 12 correct.

---

## Quick start

Requires Python 3.10+. No API key, no model downloads, ~1 minute.
**New to the project, or setting it up on a fresh machine? Start with [`SETUP.md`](SETUP.md)** —
prerequisites per OS, troubleshooting, and every command explained.

```bash
make install     # creates .venv, installs the package
make verify      # checks everything works on this machine, and says so
```

`make verify` is the one command that answers "is this actually running?". It checks the
interpreter, the install, the index (building it if absent), that a known question returns a
cited answer, that an out-of-scope question is refused, the test suite, and the evaluation
gate — then prints a verdict:

```
  [ ok ] Python 3.10 or newer  found 3.12.13
  [ ok ] Package installed  ask-my-docs 1.0.0
  [ ok ] Corpus present  8 documents in data/corpus
  [ ok ] Index built  8 documents, 23 chunks
  [ ok ] Answering works  cited 1 source(s), correct value returned
         Every formal dispute incurs a **€15.00 dispute fee**, charged when the dispute is opened. [S1]
  [ ok ] Refusing works  out-of-scope question abstained (low_relevance)
  [ ok ] Test suite  305 passed in 22.65s
  [ ok ] Evaluation gate  12/12 thresholds met on 105 questions

  THE PROJECT IS WORKING ON THIS MACHINE.
```

Every failure prints the command that fixes it. Then ask it something:

```bash
make ask Q="How long do I have to submit dispute evidence?"
```

The shipped default is the `offline` profile, so a fresh clone runs with nothing extra
installed and no environment variables set.

### If you received this as a folder rather than a git clone

Delete two directories before doing anything, then install:

```bash
rm -rf .venv storage      # Windows: rmdir /s /q .venv storage
make install && make verify
```

**`.venv` is never portable.** It hardcodes absolute paths to the machine that created it, so
a copied one is broken on arrival — `make verify` detects exactly this and tells you to remove
it. `storage/` is just a rebuildable index. Neither belongs in a shared copy, which is what
`make share` is for: it writes a ~170 KB zip with both excluded, versus 1.6 GB for the raw
folder.

```
╭─────────────────────────────── Answer ────────────────────────────────╮
│ **You have 7 calendar days from the `dispute.created` webhook to      │
│ submit evidence.** [S1]                                              │
╰───────────────────────────────────────────────────────────────────────╯
 Marker  Source                          Section                        Score
 S1      data/corpus/03-refunds-and-…    Disputes > Dispute lifecycle   0.520
confidence=0.88  grounding=1.00  model=extractive-v1  prompt=answer.v2  8ms
```

Ask something the corpus does not cover, and it declines:

```bash
make ask Q="Does Aurora support cryptocurrency payments?"
# No answer (abstained) — Reason: question_not_covered
```

### Everything else

```bash
make test              # 305 tests
make eval              # golden-set evaluation + threshold gate
make validate-golden   # check the dataset's own labels
make serve             # HTTP API on :8000, OpenAPI docs at /docs
make ui                # Streamlit demo on :8501
make lint              # ruff check + format check
make ci                # lint → validate → test → eval, exactly as CI runs it
make help              # every target
```

### Running the model-backed stack

```bash
make install-full                       # adds sentence-transformers (~500 MB) + Claude SDK

# Real embeddings + cross-encoder, no API key required:
make eval PROFILE=full-retrieval

# Add Claude generation:
cp .env.example .env                    # set ANTHROPIC_API_KEY
make eval-full
```

### Docker

```bash
docker compose up api                          # offline profile, :8000
docker compose --profile full up api-full      # Claude-backed, :8001, needs ANTHROPIC_API_KEY
docker compose --profile ui up ui              # Streamlit demo, :8501
```

---

## Ask your own documents

The web app is a React frontend (`frontend/`) served by the API itself, so it runs as one
service. Build it once, then start the API:

```bash
cd frontend && npm install && npm run build && cd ..
askmydocs serve --profile full-retrieval     # http://localhost:8000
```

To work on the frontend, run the API and the Vite dev server side by side. Vite proxies
`/library` to the API, so the browser stays on one origin and changes reload instantly:

```bash
askmydocs serve                              # terminal 1: API on :8000
cd frontend && npm run dev                   # terminal 2: http://localhost:5173
```

The older Streamlit demo (`make ui`, :8501) still works and uses the same library.

1. Drop PDF, Markdown, text, or HTML files into **Your documents** in the sidebar, or click
   **Choose files**. No document to hand? **Try with sample documents** loads the demo corpus.
2. Pick **Search in**: all documents, or just one.
3. Ask. The answer cites the file and section it came from, or the system declines and says why.

Re-uploading a filename replaces that document; the 🗑 button removes one.

**Uploads live in their own index, never in the evaluated corpus.** The evaluation harness
grades whatever is indexed under `storage_dir`, so if uploads went there, every eval run would
also search them and the retrieval metrics would move for reasons unrelated to the code. The
library keeps its files and index under `uploads.dir` instead:

```
storage/uploads/files/             the uploaded files (git-ignored), shared by every profile
storage/uploads/index/<profile>/   that profile's chunks, vectors, and manifest
```

Files are the source of truth. Each profile's index is rebuilt from them on start-up, by content
hash, so switching from `offline` to `full` re-indexes the same library with the new embedder
rather than losing it.

**Every upload is validated before it touches the library**: an allow-listed extension, a size
limit (`uploads.max_file_mb`, 20 MB by default), a sanitised filename (directory parts and
reserved device names stripped, so `..\..\x.md` cannot escape the library), and at least some
extractable text, so a scanned PDF with no text layer is rejected with that explanation. The file
is parsed from a hidden staging copy and only moved into place once it passes, so a rejected
upload leaves nothing behind.

The sidebar's **Profile** picks the engine:

| Profile | Search | Answers | Needs |
|---|---|---|---|
| Offline | hashed embeddings + keyword rerank | quoted sentences | nothing |
| Full retrieval | MiniLM embeddings + cross-encoder rerank, run locally | quoted sentences | `pip install -e ".[models]"` |
| Full | same as full retrieval | written by Claude | the above + `ANTHROPIC_API_KEY` |

The UI defaults to **Full retrieval** when its packages are installed, since it is free and
much better at matching meaning, and marks any profile this machine cannot run as
"(not set up)" with what to install. The first switch to a profile downloads its models and
re-indexes your uploads with them.

Full retrieval improves **which passages are found**, not which sentence is quoted. Both quote
modes pick the sentence by word overlap with the question, so the right passage can still yield
the wrong sentence: ask how long you have to *respond* to a dispute and Full retrieval ranks the
dispute-lifecycle passage first, then quotes its fee sentence instead of the one saying you must
*submit evidence* within 7 days. Full, where Claude reads the passage and writes the answer, is
the profile that closes that gap.

On a laptop CPU, Full retrieval takes about 3 s per question against roughly 10 ms offline.
Nearly all of it is the cross-encoder scoring ~20 candidate passages of ~600 tokens; embedding,
search, and answer assembly together take about 130 ms.

---

## Design decisions

### Why Reciprocal Rank Fusion instead of weighting the two scores

Cosine similarity is bounded in `[-1, 1]`. BM25 is unbounded and scales with corpus statistics
and query length. A weighted sum of the two needs a normalisation constant that must be
re-tuned whenever the corpus changes — a hidden hyperparameter that silently rots.

RRF discards magnitudes and fuses *ranks*:

```
score(d) = Σ  1 / (k + rank_r(d))          k = 60
         retrievers r
```

It is parameter-light (`k=60` from Cormack et al. 2009 needs no tuning), and it rewards
documents that **both** retrievers surface — the strongest signal available before spending a
cross-encoder pass. Every `RetrievedChunk` keeps its per-leg ranks and scores, so the fusion is
auditable rather than a black box:

```bash
make ask Q="rate limits" # add --show-retrieval to see dense rank, BM25 rank, and which leg won
```

### Why the keyword leg is not optional

Vector search finds paraphrases; BM25 finds the tokens a dense model blurs — `429`,
`insufficient_funds`, `Aurora-Signature`, `sk_live_`. Those are exactly what users type when
they search API documentation. Dropping the keyword leg on this corpus is not a small
regression; it is a different product.

### Five abstention gates, and why a score threshold is not enough

The first thing I tried was thresholding the reranker score. It does not work. Measured across
all 105 golden questions, the top reranked score spans **0.219–0.800 for answerable** questions
and **0.000–0.398 for unanswerable** ones. The ranges overlap, so no single cut separates "hard
question" from "unanswerable question" — which is why `min_top_score` is set low (0.10) to
reject only egregious mismatches, and the real work is done by the gate below.

What does separate them is **vocabulary**: a question the corpus cannot answer almost always
contains a pivotal term the corpus never uses. Gate 2 measures the IDF-weighted fraction of the
question's topical terms present in the corpus vocabulary, weighting an out-of-vocabulary term
at *maximum* IDF — it is by definition maximally rare, and it can never be matched. "Salesforce"
alone sinks the score.

Two details that took iteration:

- **Measured against the corpus vocabulary, not the retrieved passages.** Those are different
  failures deserving different reasons. A term the corpus lacks means out-of-scope; a term the
  corpus has but retrieval missed is a recall bug. Conflating them makes every retrieval miss
  look like an out-of-scope question and is much harder to debug.
- **A small question-scaffolding stoplist**, used *only* by this gate. "How **often** are keys
  rotated?" was falsely abstaining on an absent question word. The set contains no domain
  nouns — there is a test asserting so, because a domain noun in there would blind the gate to
  exactly the questions it exists to reject.

The remaining gates run in cost order, cheapest first: relevance floor and coverage abstain
**before the model is called at all**, which removes the most common hallucination trigger
(asking an LLM to answer from off-topic passages) and saves the latency and tokens of a request
that could only mislead. After generation, invented `[S7]` markers are stripped, an answer with
no surviving citation is discarded, and the answer's content words must overlap its cited
passages — fluent prose with no lexical footprint in its own sources is the signature of
fabrication. Every abstention carries a machine-readable reason so failures group in a
dashboard instead of being parsed out of prose.

### Prompts are configuration, not code

`config/prompts/*.yaml`, one file per `<name>.<version>`. Every `Answer` records the prompt
name, version, and content hash that produced it. `answer.v1` is still in the repository,
marked superseded, with a note recording what it cost:

> v1 asked for citations but defined no refusal sentinel, so on out-of-scope questions the model
> hedged in prose ("I'm not sure, but…") instead of emitting a token the application could
> detect. Abstention recall was 0.30 against v2's 1.00.

`latest` resolves to the highest version *not* marked superseded, so keeping the history costs
nothing at runtime.

### Chunking trade-off, measured rather than guessed

A heading is a *preferred* break, not a mandatory one. Breaking at every heading turns a
document of short sections into many thin chunks; never breaking lets a chunk span unrelated
topics. `section_break_ratio` controls how substantial a chunk must be before a heading
boundary is honoured. Swept on the golden set:

| ratio | chunks | mean tok | median tok | hit@1 | MRR | citation prec. | abstention recall |
|---|---|---|---|---|---|---|---|
| 0.0 | 60 | 194 | 167 | 0.925 | 0.958 | 0.780 | 0.917 |
| 0.4 | 44 | 263 | 252 | **0.946** | **0.970** | **0.824** | 0.917 |
| 0.6 | 32 | 362 | 372 | **0.946** | **0.970** | 0.818 | 0.917 |
| 0.8 | 24 | 482 | 499 | 0.903 | 0.947 | 0.796 | 0.917 |
| **1.0** | **23** | **505** | **607** | 0.914 | 0.950 | 0.771 | **1.000** |

**Shipped 1.0.** It is the only setting whose realised chunk sizes land in the 500–800 token
band the specification asks for, and it is the only one that refuses all 12 unanswerable
questions. It costs ~3 points of hit@1 and ~5 of citation precision against 0.4/0.6. Recall@5
is ≥0.98 across the whole range, so the reranker sees the right document either way — which is
what makes the trade acceptable. The knob is exposed in `config/app.yaml`; 0.6 is the setting
to pick if you want maximum hit@1 and do not need the larger chunks.

### The reranker score floor, also measured

`min_top_score` had to be set separately per profile, because cross-encoder logits and
lexical scores are on different scales. My first value for the cross-encoder was a guess
(`-2.0`), and running the real model showed what that cost:

| floor | citation precision | correct source | answer F1 | abstention recall | false abstention |
|---|---|---|---|---|---|
| **-10.0** | 0.801 | 0.921 | 0.409 | 1.000 | **0.043** |
| -8.0 | 0.816 | 0.932 | 0.415 | 1.000 | 0.054 |
| -6.0 | 0.843 | 0.931 | 0.410 | 1.000 | 0.065 |
| -4.0 | 0.870 | 0.942 | 0.427 | 1.000 | 0.075 |
| -2.0 (guessed) | 0.900 | 0.963 | 0.442 | 1.000 | 0.140 |

Measured score ranges: **answerable −9.46 to 8.19, unanswerable −11.22 to 1.11**. They
overlap, so this floor cannot separate the two classes — which is the same conclusion the
lexical profile reached, and the reason the coverage gate exists. Note **abstention recall is
1.000 at every floor**: the coverage gate is doing all of the out-of-scope work, and the floor
contributes none of it.

Shipped **−10.0**, just below the observed answerable minimum, so the floor rejects only
egregious mismatch as designed. The tighter `-2.0` buys +0.10 citation precision at 3.3× the
false-abstention rate — refusing 1 in 7 answerable questions is a worse product than citing a
slightly wider set of sources.

### Three profiles, one code path

| | `offline` (default, CI) | `full-retrieval` | `full` |
|---|---|---|---|
| Embeddings | hashed n-gram + word features | `all-MiniLM-L6-v2` | `all-MiniLM-L6-v2` |
| Vector store | numpy (exact cosine) | ChromaDB | ChromaDB |
| Reranker | IDF-weighted lexical | ms-marco cross-encoder | ms-marco cross-encoder |
| Generator | deterministic extractive | deterministic extractive | `claude-opus-5` |
| Needs | nothing | ~500 MB of models | models + an API key |
| Benchmarked | yes | yes | generation not yet |

`full-retrieval` exists so retrieval can be measured against real models without an API key,
which is what isolates a retrieval regression from a generation one. It is also a legitimate
deployment for anyone who wants better recall without paying per query.

Both run through identical retrieval, fusion, citation-enforcement, and evaluation code. The
offline profile is not a stub — it is what makes the eval suite a real gate rather than a script
someone runs occasionally. A metric you cannot run on every commit cannot gate a build.

Score *scales* differ by reranker (cross-encoder logits are unbounded; lexical scores are
`[0,1]`), so abstention thresholds are configured **per profile**, not globally.

---

## Three bugs worth reading about

Each was found by a failing golden question, not by reading code.

**1. Headings vanished from the index.** "How often are encryption data keys rotated?" was
falsely abstaining. The word "encryption" appears exactly once in the whole corpus — in an `##`
heading. The chunker recorded heading text only in the breadcrumb of the chunk that *started* at
it, so a heading falling mid-chunk was dropped from the body *and* the metadata, making the term
permanently unretrievable. Headings are now emitted as first-class units.

**2. Sentences were fragmenting two different ways.** Markdown hard-wraps prose, so splitting on
`\n` emitted `"This is"` as its own sentence. And markdown puts the period *inside* the emphasis
span — `**…submit evidence.** This is…` — so a `(?<=[.!?])\s+` lookbehind sees `*` before the
space and never splits at all. The two sentences merged into one long span, diluting every
density-based relevance score, and a weaker sentence won extraction. Fixed by joining
soft-wrapped lines while keeping headings and table rows self-contained, plus a lookbehind
alternation that tolerates closing emphasis.

**3. Morphology was zeroing the keyword leg.** A user types "rate limited"; the document says
"rate limits". No match on the most discriminative term in the query, and BM25 contributed
nothing. Added a compact suffix stemmer shared by BM25, reranking, grounding, and the evaluation
metrics — one vocabulary across every lexical comparison, since mixing stemmed and unstemmed
keys silently breaks IDF weighting. Its contract is *consistency*, not linguistic correctness:
"rotate", "rotated", and "rotation" all collapse to `rotat`, which is all a matcher needs.

---

## Evaluation harness

```bash
python -m eval.validate_golden          # check the labels first
python -m eval.run_eval --profile offline
python -m eval.run_eval --baseline eval_reports/main.json   # deltas vs a previous run
python -m eval.run_eval --llm-judge     # add LLM-as-judge faithfulness (needs a key)
python -m eval.ragas_eval --limit 25    # third-party cross-check
```

**The dataset validates itself.** A golden set with wrong labels poisons every downstream
number: a mislabelled source looks like a retrieval regression, and an "unanswerable" question
the corpus actually answers looks like a hallucination. `validate_golden` checks schema, unique
IDs, source existence, and that each reference answer's vocabulary overlaps the *union* of its
declared sources — union, because `expected_sources` uses any-of semantics, so a fact restated
in two documents legitimately lists both. It also warns when an "unanswerable" question's terms
all appear in the corpus.

It caught two rows on its first run. Both turned out to be false alarms from very short answers
("Node 18." — two content tokens, where one incidental word swings the ratio by 50 points), so
the check now skips answers under four content tokens rather than training people to ignore it.

**Ragas is a cross-check, not the gate.** It costs an API call per question, so it cannot run on
every commit. The in-house metrics run offline in 1.4 seconds; Ragas is there for a second
opinion before changing a threshold.

CI runs lint → golden validation → tests (Python 3.10/3.11/3.12) → the evaluation gate → a
Docker build with a smoke test that indexes and answers inside the image. The evaluation job
posts its report to the job summary and updates a single PR comment in place.

---

## API

```bash
make serve   # OpenAPI docs at http://localhost:8000/docs
```

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/ask` | Answer a question, or abstain with a reason |
| `POST` | `/ingest` | Index files, directories, or URLs |
| `GET` | `/healthz` | Liveness — reports an init failure instead of hiding it |
| `GET` | `/readyz` | Readiness — **503 while the index is empty**, since such a node could only abstain |
| `GET` | `/stats` | Index and configuration state |
| `GET` | `/prompts` | Prompt registry and the active version |
| `GET` | `/library/documents` | List uploaded documents |
| `POST` | `/library/documents` | Upload one file (multipart `file`) and index it |
| `DELETE` | `/library/documents/{doc_id}` | Remove a document and its file |
| `POST` | `/library/ask` | Answer from uploads; `doc_ids` limits it to chosen documents |
| `POST` | `/library/samples` | Add the bundled sample documents (repeatable) |
| `GET` | `/library/info` | Active profile, models, answer mode (`quote`/`generate`), upload limits |

```bash
curl -s localhost:8000/ask -H 'content-type: application/json' \
  -d '{"question":"What is the dispute fee?","include_retrieval":true}' | jq
```

```bash
curl -s localhost:8000/library/documents -F file=@handbook.pdf | jq     # -> {"doc_id": ...}
curl -s localhost:8000/library/ask -H 'content-type: application/json' \
  -d '{"question":"What is the notice period?","doc_ids":["<doc_id>"]}' | jq
```

Every answer carries two timings: `latency_ms` is generation alone, `total_ms` the whole
question. They differ a lot on a CPU, where the cross-encoder dominates full retrieval.

When `api.frontend_dir` (default `frontend/dist`) holds a built frontend, the API serves it at
`/` as well, so one process serves both. API routes always win; browser navigations to any other
path get `index.html` for client-side routing, while other clients still get a JSON 404.

A rejected upload gets a specific status: `415` unsupported type, `413` over the size limit,
`409` a second file claiming an existing `doc_id`, `422` no readable text.

Set `ASKMYDOCS_API_KEY` to require an `X-API-Key` header on `/ask`, `/ingest`, and the library
endpoints that upload, delete, or ask; the ops endpoints stay open so health checks keep working
without the secret.

---

## Layout

```
src/askmydocs/
  config.py          typed config: YAML → profile → env → overrides
  text.py            tokenising, stemming, sentence splitting, similarity
  models.py          Pydantic types shared by the API, stores, and eval
  pipeline.py        the facade; ingestion, manifest, query path
  library.py         upload library: validation, its own index, per-document scoping
  ingest/            loaders (md/pdf/html/txt) · structural chunker
  index/             chunk store · embeddings · vector stores · BM25 · RRF fusion
  rerank/            cross-encoder · Cohere · IDF-lexical
  generation/        prompt registry · LLM providers · citation enforcement
  api/main.py        FastAPI application
  cli.py             askmydocs command
config/
  app.yaml           baseline + offline/full profiles
  prompts/           answer.v1 (superseded) · answer.v2 (active) · judge.v1
data/
  corpus/            8-document demo corpus
  golden/            105 validated question-answer pairs
scripts/verify.py    one-command installation check
eval/                metrics · runner + gate · label validator · Ragas cross-check
tests/               305 tests
frontend/            React + TypeScript web app (Vite, Tailwind, TanStack Query)
ui/streamlit_app.py  Streamlit demo UI
```

### Ingestion is incremental

The index manifest records each document's content hash, so re-running ingestion re-embeds only
what changed. It also records the **embedder's fingerprint**: querying an index built with a
different embedding model returns confident nonsense, so that mismatch raises
`IndexMismatchError` at startup with instructions, rather than silently degrading retrieval.

---

## Limitations

- **Claude generation is unverified.** The retrieval half of the `full` profile is measured
  (see `full-retrieval`), but `claude-opus-5` has never been called — no API key was available.
  Everything up to the request builds and runs; a missing credential now produces an actionable
  error rather than a raw SDK `TypeError`.
- **The demo corpus is synthetic** — eight documents about a fictional payments platform, written
  so every golden answer is verifiable against a specific line. That makes the evaluation
  deterministic and the labels auditable, but it is not a substitute for measuring on your own
  documents. Point `corpus_dir` at them and re-baseline `eval/thresholds.yaml`.
- **BM25 is rebuilt in memory at startup** from the chunk store. Deliberate — it makes
  keyword/vector divergence structurally impossible — but it caps the corpus at what fits in
  memory. Past that, move to a persistent keyword index.
- **Grounding is lexical, not entailment.** It catches fabrication that reuses no source
  vocabulary. It cannot catch a claim that reuses source words to state something the source
  does not; `--llm-judge` exists for that.
- **The out-of-scope gate is vocabulary-based**, so a question whose every term appears in the
  corpus but in an unrelated combination can slip through. On the golden set the refusal
  sentinel catches those; without an LLM there is no second line of defence.
- **No reranker score calibration.** Thresholds are tuned per profile on this corpus and would
  need re-tuning on another. That includes the upload library, which uses the same thresholds:
  on very different documents, expect some over- or under-refusal.
- **The upload library is single-user.** One lock serialises uploads and questions, which is
  correct for one person's documents but not a multi-tenant service; that would want per-user
  libraries and a real database.

## License

MIT
