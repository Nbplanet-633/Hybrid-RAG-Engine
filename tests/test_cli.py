"""CLI tests.

The CLI is a real user surface, and its error paths matter most: a stack trace on
a missing index or a bad profile is a worse experience than a one-line message
that says what to run next.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from askmydocs.cli import app

REPO_ROOT = Path(__file__).resolve().parents[1]
runner = CliRunner()


@pytest.fixture
def cli_env(tmp_path: Path, corpus_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("ASKMYDOCS_PROFILE", "offline")
    monkeypatch.setenv("ASKMYDOCS_CONFIG", str(REPO_ROOT / "config" / "app.yaml"))
    monkeypatch.setenv("ASKMYDOCS_STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("ASKMYDOCS_CORPUS_DIR", str(corpus_dir))
    return corpus_dir


class TestIngestCommand:
    def test_ingest_reports_what_it_indexed(self, cli_env: Path) -> None:
        result = runner.invoke(app, ["ingest", "--reset"])
        assert result.exit_code == 0
        assert "Ingestion complete" in result.stdout
        assert "Documents indexed" in result.stdout

    def test_second_run_reports_skipped_documents(self, cli_env: Path) -> None:
        runner.invoke(app, ["ingest", "--reset"])
        result = runner.invoke(app, ["ingest"])
        assert result.exit_code == 0
        assert "unchanged" in result.stdout

    def test_missing_target_exits_non_zero_without_a_traceback(self, cli_env: Path) -> None:
        result = runner.invoke(app, ["ingest", "/definitely/not/here"])
        assert result.exit_code != 0
        assert "Traceback" not in result.stdout


class TestAskCommand:
    def test_answer_and_citations_are_printed(self, cli_env: Path) -> None:
        runner.invoke(app, ["ingest", "--reset"])
        result = runner.invoke(app, ["ask", "How long do I have to request a refund?"])
        assert result.exit_code == 0
        assert "Answer" in result.stdout
        assert "Citations" in result.stdout
        assert "14" in result.stdout

    def test_abstention_is_clearly_labelled(self, cli_env: Path) -> None:
        runner.invoke(app, ["ingest", "--reset"])
        result = runner.invoke(app, ["ask", "What is the capital of Portugal?"])
        assert result.exit_code == 0
        assert "abstained" in result.stdout.lower()
        assert "Reason:" in result.stdout

    def test_retrieval_trace_flag(self, cli_env: Path) -> None:
        runner.invoke(app, ["ingest", "--reset"])
        result = runner.invoke(app, ["ask", "refund window", "--show-retrieval"])
        assert result.exit_code == 0
        assert "Retrieval trace" in result.stdout

    def test_json_output_is_machine_readable(self, cli_env: Path) -> None:
        import json

        runner.invoke(app, ["ingest", "--reset"])
        result = runner.invoke(app, ["ask", "refund window", "--json"])
        assert result.exit_code == 0
        # Rich pretty-prints JSON, so parse it back to prove it is valid.
        payload = json.loads(result.stdout)
        assert "question" in payload and "abstained" in payload

    def test_asking_with_an_empty_index_says_what_to_run(self, cli_env: Path) -> None:
        result = runner.invoke(app, ["ask", "anything"])
        assert result.exit_code == 2
        assert "ingest" in result.stdout


class TestInspectionCommands:
    def test_stats(self, cli_env: Path) -> None:
        runner.invoke(app, ["ingest", "--reset"])
        result = runner.invoke(app, ["stats"])
        assert result.exit_code == 0
        assert "offline" in result.stdout

    def test_stats_json(self, cli_env: Path) -> None:
        runner.invoke(app, ["ingest", "--reset"])
        result = runner.invoke(app, ["stats", "--json"])
        assert result.exit_code == 0
        assert "chunks" in result.stdout

    def test_prompts_lists_versions_and_status(self, cli_env: Path) -> None:
        result = runner.invoke(app, ["prompts"])
        assert result.exit_code == 0
        assert "answer" in result.stdout
        assert "superseded" in result.stdout
        assert "Active:" in result.stdout

    def test_version(self) -> None:
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert result.stdout.strip()

    def test_help_lists_every_command(self) -> None:
        result = runner.invoke(app, ["--help"])
        for command in ("ingest", "ask", "stats", "prompts", "serve", "version"):
            assert command in result.stdout


class TestErrorHandling:
    def test_serve_refuses_a_profile_that_is_not_set_up(
        self, cli_env: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from askmydocs import config as config_module

        monkeypatch.setattr(config_module.importlib.util, "find_spec", lambda name: None)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        started = []
        monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: started.append(args))

        result = runner.invoke(app, ["serve", "--profile", "full"])
        assert result.exit_code == 1
        assert "isn't set up" in result.stdout
        assert "ANTHROPIC_API_KEY" in result.stdout
        assert started == []  # the server never started

    def test_unknown_profile_exits_cleanly(self, cli_env: Path) -> None:
        result = runner.invoke(app, ["stats", "--profile", "nonsense"])
        assert result.exit_code == 2
        assert "Failed to initialise" in result.stdout
        assert "Traceback" not in result.stdout
