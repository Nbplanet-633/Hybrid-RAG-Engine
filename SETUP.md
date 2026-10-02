# Setup & Run Guide

Everything needed to get **Ask My Docs** running on a new machine, start to finish.

If you only read one thing: install, then run `make verify`. It tells you whether the project
is working and, if not, exactly which command fixes it.

```bash
make install
make verify
```

---

## Table of contents

1. [What this project is](#1-what-this-project-is)
2. [What you need installed](#2-what-you-need-installed)
3. [Getting the code](#3-getting-the-code)
4. [Setup — two commands](#4-setup--two-commands)
5. [Confirming it works](#5-confirming-it-works)
6. [Using it](#6-using-it)
7. [Troubleshooting](#7-troubleshooting)
8. [Optional: the model-backed stack](#8-optional-the-model-backed-stack)
9. [Running it with Docker](#9-running-it-with-docker)
10. [Putting it on GitHub](#10-putting-it-on-github)
11. [Before you present this](#11-before-you-present-this)

---

## 1. What this project is

A Retrieval-Augmented Generation (RAG) system that answers questions about a document corpus
**and refuses to answer when the documents do not support an answer**. Every answer it returns
carries a citation pointing at the source passage.

It ships with a demo corpus (8 documents about a fictional payments platform) and a
105-question evaluation set that runs as a regression gate.

**You do not need an API key to run it.** The default configuration uses a fully local,
deterministic stack. An optional profile adds transformer models and Claude.

---

## 2. What you need installed

Three things. Check each with the command in the middle column.

| Requirement | Check it | Install it |
|---|---|---|
| **Python 3.10 or newer** | `python3 --version` | **macOS:** `brew install python@3.12`<br>**Ubuntu/Debian:** `sudo apt install python3.12 python3.12-venv`<br>**Windows:** [python.org/downloads](https://www.python.org/downloads/) — tick *"Add Python to PATH"* |
| **git** | `git --version` | **macOS:** `xcode-select --install`<br>**Ubuntu:** `sudo apt install git`<br>**Windows:** [git-scm.com](https://git-scm.com/download/win) |
| **make** | `make --version` | **macOS:** `xcode-select --install`<br>**Ubuntu:** `sudo apt install build-essential`<br>**Windows:** not native — see the note below |

> **Windows users:** the simplest path is **WSL2** (`wsl --install` in PowerShell as admin,
> then use the Ubuntu terminal). Everything in this guide then works unchanged. If you would
> rather not use WSL, section 4 has the equivalent commands without `make`.

You do **not** need: an API key, Docker, a GPU, or any model downloads.

---

## 3. Getting the code

**If you received a zip file**, unzip it and open a terminal in that folder:

```bash
unzip ask-my-docs-share.zip -d ask-my-docs
cd ask-my-docs
```

**If you received the whole project folder** (copied from a drive, USB, or shared disk), delete
two directories before doing anything else:

```bash
cd ask-my-docs
rm -rf .venv storage          # Windows CMD: rmdir /s /q .venv storage
```

> **Why this matters.** `.venv` is a Python virtual environment. It hardcodes absolute file
> paths belonging to the machine that created it, so a copied one is broken on arrival and
> produces confusing errors that look like bugs in the project. It is also ~1.6 GB. `storage/`
> is just a search index that gets rebuilt in under a second. Neither should ever be copied
> between machines — `make verify` detects this specific mistake and tells you to remove them.

**If you are cloning from GitHub**, nothing to clean up:

```bash
git clone <repository-url>
cd ask-my-docs
```

---

## 4. Setup — two commands

```bash
make install
make verify
```

`make install` creates an isolated `.venv` folder and installs the package plus its
dependencies. It takes about a minute and downloads roughly 100 MB. It automatically picks the
newest Python 3.10+ on your system.

`make verify` is covered in the next section.

<details>
<summary><b>Windows without WSL — the same steps without <code>make</code></b></summary>

```bat
python -m venv .venv
.venv\Scripts\pip install -e ".[loaders,chroma,dev]"
.venv\Scripts\python scripts\verify.py
```

Then substitute `.venv\Scripts\askmydocs` wherever this guide says `make ask` / `make serve`:

```bat
.venv\Scripts\askmydocs ingest --reset
.venv\Scripts\askmydocs ask "How much is the dispute fee?"
.venv\Scripts\askmydocs serve
```
</details>

---

## 5. Confirming it works

```bash
make verify
```

This is the command that answers *"is the project actually running on this machine?"*. It
checks the interpreter, the installation, the document corpus, builds the search index if it is
missing, asks a question with a known answer, confirms an out-of-scope question is refused, runs
the test suite, and runs the evaluation gate.

**Expected output:**

```
Ask My Docs — installation check

  [ ok ] Python 3.10 or newer  found 3.12.13
  [ ok ] Package installed  ask-my-docs 1.0.0
  [ ok ] Configuration loads  profile 'offline'
  [ ok ] Corpus present  8 documents in data/corpus
  [ ok ] Pipeline builds  lexical reranker, extractive:extractive-v1
  [ ok ] Index built  8 documents, 23 chunks
  [ ok ] Answering works  cited 1 source(s), correct value returned
         Every formal dispute incurs a **€15.00 dispute fee**, charged when the dispute is opened. [S1]
  [ ok ] Refusing works  out-of-scope question abstained (low_relevance)
  [ ok ] Test suite  257 passed in 0.85s
  [ ok ] Evaluation gate  12/12 thresholds met on 105 questions

  THE PROJECT IS WORKING ON THIS MACHINE.
```

If any line says `[FAIL]`, it prints the exact command to fix it. Section 7 covers the common
ones. Takes about 15 seconds; `make verify` accepts `--quick` via
`.venv/bin/python scripts/verify.py --quick` to skip the tests and gate.

---

## 6. Using it

### Ask a question

```bash
make ask Q="How much is the dispute fee?"
```

```
╭─────────────────────────────── Answer ────────────────────────────────╮
│ Every formal dispute incurs a **€15.00 dispute fee**, charged when    │
│ the dispute is opened. [S1]                                          │
╰───────────────────────────────────────────────────────────────────────╯
 Marker  Source                          Section                        Score
 S1      data/corpus/03-refunds-and-…    Disputes > Dispute lifecycle   0.520
confidence=0.88  grounding=1.00  prompt=answer.v2  8ms
```

### See it refuse

This is the behaviour the project is built around — ask something the documents do not cover:

```bash
make ask Q="Does Aurora support cryptocurrency payments?"
```

```
╭─────────────────────── No answer (abstained) ────────────────────────╮
│ I can't answer that from the indexed documents.                      │
│ Reason: question_not_covered                                         │
╰──────────────────────────────────────────────────────────────────────╯
```

### Inspect how retrieval worked

```bash
.venv/bin/askmydocs ask "rate limits" --show-retrieval
```

Shows every retrieved passage with its dense rank, BM25 rank, and which retriever surfaced it.

### Run the HTTP API

```bash
make serve
```

Then open **http://localhost:8000/docs** for interactive API documentation, or:

```bash
curl -s localhost:8000/ask -H 'content-type: application/json' \
  -d '{"question":"What is the minimum payout amount?"}'
```

| Endpoint | Purpose |
|---|---|
| `POST /ask` | Answer a question, or abstain with a reason |
| `POST /ingest` | Index files, directories, or URLs |
| `GET /healthz` | Liveness check |
| `GET /readyz` | Readiness — returns 503 while the index is empty |
| `GET /stats` | Index and configuration state |
| `GET /prompts` | Prompt registry and active version |

### Run the evaluation

```bash
make eval      # 105 questions, 12 thresholds, ~1.4 seconds
make test      # 257 tests
make ci        # lint + label validation + tests + eval, as CI runs it
```

### Index your own documents

Drop `.md`, `.txt`, `.pdf`, or `.html` files into `data/corpus/` and re-index:

```bash
make ingest ARGS=--reset
```

Or point it anywhere:

```bash
.venv/bin/askmydocs ingest /path/to/your/docs --reset
```

> The evaluation set in `data/golden/` is written against the demo corpus. If you replace the
> documents, `make eval` will fail until you write question–answer pairs for the new ones.

### All available commands

```bash
make help
```

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ERROR: no Python >= 3.10 found on PATH` | Python missing or too old | Install Python 3.10+ (section 2), or point at one: `make install PYTHON=/full/path/to/python3` |
| `make: command not found` | `make` not installed | macOS: `xcode-select --install` · Ubuntu: `sudo apt install build-essential` · Windows: use WSL or the commands in section 4 |
| `[FAIL] virtualenv belongs to this machine` | `.venv` was copied from another computer | `rm -rf .venv && make install` |
| `[FAIL] Package installed — cannot import askmydocs` | Install did not complete | Re-run `make install` and read the error it prints |
| `The index is empty` | Documents not indexed yet | `make ingest ARGS=--reset` |
| `409 Index is empty. POST /ingest first` | Same, hitting the API | `make ingest ARGS=--reset`, then restart `make serve` |
| `RuntimeError: ... requires an extra install` | Using a profile that needs optional models | `make install-full`, or stay on the default: `unset ASKMYDOCS_PROFILE` |
| `No Anthropic credentials found` | Using the `full` profile without a key | Set `ANTHROPIC_API_KEY` (section 8), or use the default profile |
| `IndexMismatchError` | Index built with a different embedding model | `make ingest ARGS=--reset` |
| Port 8000 already in use | Something else is on that port | `make serve` then edit `config/app.yaml`, or `.venv/bin/askmydocs serve --port 8080` |

Start over from a clean slate at any time:

```bash
make clean
rm -rf .venv
make install && make verify
```

---

## 8. Optional: the model-backed stack

The default configuration is deliberately dependency-light. Two heavier options exist.

### Real embeddings and a cross-encoder reranker — no API key needed

```bash
make install-full                    # adds ~500 MB of models on first run
make eval PROFILE=full-retrieval
```

This swaps in `sentence-transformers/all-MiniLM-L6-v2` embeddings, ChromaDB, and a
`ms-marco-MiniLM-L-6-v2` cross-encoder. Model weights download automatically the first time.

### Add Claude for answer generation — needs an API key

```bash
cp .env.example .env
# edit .env and set ANTHROPIC_API_KEY=sk-ant-...
export ASKMYDOCS_PROFILE=full
make ingest ARGS=--reset
make eval-full
```

Get a key at [console.anthropic.com](https://console.anthropic.com/). This path costs money per
query.

### The web app

Needs [Node.js](https://nodejs.org/) 20 or newer, once, to build the React frontend:

```bash
make ui               # npm ci && npm run build, in frontend/
make serve            # API and web app together on http://localhost:8000
```

To change the frontend with instant reload, run `make serve` in one terminal and
`cd frontend && npm run dev` in another, then open http://localhost:5173.

### Switching profiles

| Profile | Command | Needs |
|---|---|---|
| `offline` (default) | nothing | nothing |
| `full-retrieval` | `export ASKMYDOCS_PROFILE=full-retrieval` | `make install-full` |
| `full` | `export ASKMYDOCS_PROFILE=full` | `make install-full` + API key |

Return to the default with `unset ASKMYDOCS_PROFILE`.

---

## 9. Running it with Docker

Requires [Docker Desktop](https://www.docker.com/products/docker-desktop/).

```bash
docker compose up api          # API on http://localhost:8000
```

The image indexes the corpus on first boot and stores it in a named volume, so restarts are
instant. To rebuild after code changes:

```bash
docker compose up api --build
```

---

## 10. Putting it on GitHub

```bash
git init -b main                     # skip if the folder already has a .git directory
git add -A
git commit -m "Ask My Docs: RAG system with enforced citations"
```

Create an empty repository at **https://github.com/new** — do **not** tick "Add a README",
".gitignore", or "licence", because the project already includes all three and adding them
creates a conflicting commit. Then:

```bash
git remote add origin https://github.com/<your-username>/ask-my-docs.git
git push -u origin main
```

`.gitignore` already excludes `.venv/`, `storage/`, and `.env`, so none of those get committed.

The repository includes a GitHub Actions workflow (`.github/workflows/ci.yml`) that runs
automatically on every push: linting, dataset validation, the test suite across Python 3.10,
3.11 and 3.12, the evaluation gate, and a Docker build.

---

## 11. Before you present this

Read **`README.md`** properly. It documents *why* each design decision was made, not just what
the system does — and those are the questions that get asked:

- Why Reciprocal Rank Fusion instead of a weighted score sum
- Why a relevance-score threshold alone cannot detect unanswerable questions, with the measured
  score ranges that prove it
- Two parameter sweeps and the trade-offs behind the values that shipped
- Three retrieval bugs, how each was found, and what each cost
- A "Where it fails" section naming the 4 false abstentions and 1 retrieval miss by ID

Know these numbers and where they come from:

| Metric | Value |
|---|---|
| recall@5 · MRR · hit@1 | 0.989 · 0.950 · 0.914 |
| Answers carrying a resolvable citation | 100% |
| Unanswerable questions correctly refused | 12/12 |
| False-abstention rate | 4.3% |
| Tests · coverage | 257 · 86% |

One honest caveat worth knowing before anyone asks: those figures come from the **`offline`
profile** and the model-backed **`full-retrieval`** profile, both of which were measured. The
`full` profile's Claude generation path has **not** been benchmarked — the README states this
explicitly, and `eval/thresholds.yaml` marks the affected thresholds as estimates rather than
measurements.
