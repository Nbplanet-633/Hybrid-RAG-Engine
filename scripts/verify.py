#!/usr/bin/env python3
"""Check whether Ask My Docs is working on THIS machine.

Answers one question — "is the project actually running here?" — with a yes or a
no and, when it is a no, the exact command that fixes it.

    python scripts/verify.py          # full check (~15 s)
    python scripts/verify.py --quick  # skip the test suite and evaluation gate

Deliberately dependency-free at the top: the first few checks run on the standard
library alone, so this script still produces a useful diagnosis when the package
is not installed yet. Exits 0 if everything passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

GREEN, RED, YELLOW, DIM, BOLD, RESET = (
    ("\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m")
    if sys.stdout.isatty()
    else ("", "", "", "", "", "")
)

results: list[tuple[bool, str, str]] = []


def check(ok: bool, label: str, detail: str = "", fix: str = "") -> bool:
    mark = f"{GREEN} ok {RESET}" if ok else f"{RED}FAIL{RESET}"
    print(f"  [{mark}] {label}" + (f"  {DIM}{detail}{RESET}" if detail else ""))
    if not ok and fix:
        print(f"         {YELLOW}fix: {fix}{RESET}")
    results.append((ok, label, fix))
    return ok


def warn(label: str, detail: str = "") -> None:
    print(f"  [{YELLOW}warn{RESET}] {label}" + (f"  {DIM}{detail}{RESET}" if detail else ""))


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the installation.")
    parser.add_argument("--quick", action="store_true", help="Skip the test suite and eval gate.")
    args = parser.parse_args()

    print(f"\n{BOLD}Ask My Docs — installation check{RESET}")
    print(f"{DIM}{REPO}{RESET}\n")

    # --- 1. interpreter -----------------------------------------------------
    version = ".".join(str(n) for n in sys.version_info[:3])
    if not check(
        sys.version_info >= (3, 10),
        "Python 3.10 or newer",
        f"found {version}",
        "install Python 3.10+ (macOS: brew install python@3.12), then: make install",
    ):
        return report()

    # A copied .venv from another machine has absolute paths baked in and will
    # not work here. This is the single most common failure when the project is
    # shared as a folder rather than cloned.
    in_venv = sys.prefix != sys.base_prefix
    if not in_venv:
        warn(
            "not running inside the project virtualenv",
            "use .venv/bin/python, or run: make verify",
        )
    venv_python = REPO / ".venv" / ("Scripts" if os.name == "nt" else "bin") / "python"
    if venv_python.exists():
        try:
            marker = (REPO / ".venv" / "pyvenv.cfg").read_text(encoding="utf-8")
            broken = not Path(
                next(
                    (
                        ln.split("=", 1)[1].strip()
                        for ln in marker.splitlines()
                        if ln.startswith("home")
                    ),
                    "",
                )
            ).exists()
            if broken:
                check(
                    False,
                    "virtualenv belongs to this machine",
                    "it references an interpreter that does not exist here",
                    "rm -rf .venv && make install    (a copied .venv is never portable)",
                )
                return report()
        except (OSError, StopIteration):
            pass

    # --- 2. package ---------------------------------------------------------
    try:
        import askmydocs

        check(True, "Package installed", f"ask-my-docs {askmydocs.__version__}")
    except ImportError:
        check(
            False,
            "Package installed",
            "cannot import askmydocs",
            "make install    (or: pip install -e '.[loaders,chroma,dev]')",
        )
        return report()

    from askmydocs.config import load_config
    from askmydocs.pipeline import RAGPipeline

    # --- 3. configuration and prompts --------------------------------------
    try:
        config = load_config()
        check(True, "Configuration loads", f"profile '{config.profile}'")
    except Exception as exc:  # noqa: BLE001
        check(False, "Configuration loads", f"{type(exc).__name__}: {exc}", "check config/app.yaml")
        return report()

    corpus = Path(config.corpus_dir)
    documents = sorted(corpus.glob("*.md")) if corpus.exists() else []
    if not check(
        bool(documents),
        "Corpus present",
        f"{len(documents)} documents in {corpus}",
        f"the corpus should be at {corpus} — re-download the project folder",
    ):
        return report()

    # --- 4. pipeline, building the index if needed --------------------------
    try:
        pipeline = RAGPipeline.from_config(config)
    except Exception as exc:  # noqa: BLE001
        check(
            False,
            "Pipeline builds",
            f"{type(exc).__name__}: {exc}",
            "if this mentions an extra install, run: make install",
        )
        return report()
    check(
        True,
        "Pipeline builds",
        f"{pipeline.stats()['reranker']} reranker, {pipeline.stats()['generator']}",
    )

    if pipeline.is_empty():
        print(f"  {DIM}index is empty — building it now...{RESET}")
        try:
            pipeline.ingest(reset=True)
        except Exception as exc:  # noqa: BLE001
            check(False, "Index builds", f"{type(exc).__name__}: {exc}", "make ingest ARGS=--reset")
            return report()
    stats = pipeline.stats()
    check(
        stats["chunks"] > 0,
        "Index built",
        f"{stats['documents']} documents, {stats['chunks']} chunks",
        "make ingest ARGS=--reset",
    )

    # --- 5. the two behaviours that actually matter -------------------------
    answer = pipeline.answer("What is the dispute fee?")
    check(
        not answer.abstained and bool(answer.citations) and "15.00" in answer.text,
        "Answering works",
        f"cited {len(answer.citations)} source(s), correct value returned",
        "retrieval or generation is misconfigured — run: make eval",
    )
    if not answer.abstained:
        print(f"         {DIM}{answer.text[:96]}{RESET}")

    refusal = pipeline.answer("What is the capital of Portugal?")
    check(
        refusal.abstained,
        "Refusing works",
        f"out-of-scope question abstained ({refusal.abstain_reason})",
        "the abstention gates are misconfigured — check config/app.yaml",
    )

    if args.quick:
        return report()

    # --- 6. test suite ------------------------------------------------------
    python = str(venv_python) if venv_python.exists() else sys.executable
    # No extra -q here: pyproject already sets it in addopts, and a second one
    # becomes -qq, which suppresses the very summary line we want to report.
    tests = subprocess.run(  # noqa: S603
        [python, "-m", "pytest", "--tb=no", "-p", "no:cacheprovider"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    summary = next(
        (
            ln.strip("= ")
            for ln in reversed(tests.stdout.splitlines())
            if ("passed" in ln or "failed" in ln) and "warning" not in ln.lower()[:20]
        ),
        "",
    )
    check(
        tests.returncode == 0, "Test suite", summary.strip(), "make test    (to see the failures)"
    )

    # --- 7. evaluation gate ------------------------------------------------
    gate = subprocess.run(  # noqa: S603
        [python, "-m", "eval.run_eval", "--profile", config.profile, "--quiet"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    passed = sum(1 for ln in gate.stdout.splitlines() if "[ok  ]" in ln)
    total = sum(1 for ln in gate.stdout.splitlines() if "[ok  ]" in ln or "[FAIL]" in ln)
    golden = REPO / "data" / "golden" / "golden_set.jsonl"
    questions = sum(1 for line in golden.read_text(encoding="utf-8").splitlines() if line.strip())
    check(
        gate.returncode == 0,
        "Evaluation gate",
        f"{passed}/{total} thresholds met on {questions} questions",
        "make eval    (to see which metric regressed)",
    )

    return report()


def report() -> int:
    failures = [(label, fix) for ok, label, fix in results if not ok]
    print()
    if not failures:
        print(f"{GREEN}{BOLD}  THE PROJECT IS WORKING ON THIS MACHINE.{RESET}")
        print(
            f'{DIM}  Try it:   make ask Q="How long do I have to submit dispute evidence?"{RESET}'
        )
        print(f"{DIM}  Serve it: make serve      then open http://localhost:8000/docs{RESET}\n")
        return 0

    print(f"{RED}{BOLD}  NOT WORKING YET — {len(failures)} check(s) failed.{RESET}")
    for label, fix in failures:
        print(f"    - {label}" + (f"  ->  {fix}" if fix else ""))
    print()
    return 1


if __name__ == "__main__":
    if shutil.which("git") is None:
        pass  # git is not required to run, only to clone
    sys.exit(main())
