"""Prompt registry tests.

Prompts are versioned configuration. These tests pin the guarantees the rest of
the system relies on: that ``latest`` skips superseded versions, that a missing
template variable fails loudly at render time rather than producing a prompt with
a literal ``{context}`` in it, and that a prompt's identity hash changes when its
text does.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from askmydocs.generation.prompts import MissingPromptVariable, Prompt, PromptLibrary

REPO_PROMPTS = Path(__file__).resolve().parents[1] / "config" / "prompts"


@pytest.fixture
def library() -> PromptLibrary:
    return PromptLibrary(REPO_PROMPTS)


def _write(directory: Path, name: str, body: str) -> None:
    (directory / name).write_text(body, encoding="utf-8")


class TestShippedPrompts:
    def test_loads_the_repository_prompts(self, library: PromptLibrary) -> None:
        assert "answer" in library.names()
        assert "judge" in library.names()
        assert library.versions("answer") == ["v1", "v2"]

    def test_latest_skips_superseded_versions(self, library: PromptLibrary) -> None:
        # answer.v1 is kept for reproducibility but marked superseded, so `latest`
        # must resolve to v2 rather than simply the highest number.
        assert library.get("answer", "latest").version == "v2"

    def test_explicit_version_still_resolves_a_superseded_prompt(
        self, library: PromptLibrary
    ) -> None:
        assert library.get("answer", "v1").version == "v1"
        assert library.get("answer", "v1").status == "superseded"

    def test_active_answer_prompt_declares_the_expected_variables(
        self, library: PromptLibrary
    ) -> None:
        assert library.get("answer", "latest").template_variables == {
            "question",
            "context",
            "refusal_marker",
        }

    def test_unknown_name_and_version_raise_with_the_alternatives(
        self, library: PromptLibrary
    ) -> None:
        with pytest.raises(KeyError, match="No prompt named"):
            library.get("nonexistent")
        with pytest.raises(KeyError, match="Available versions"):
            library.get("answer", "v99")

    def test_describe_is_serialisable_for_the_api(self, library: PromptLibrary) -> None:
        entries = library.describe()
        assert entries
        for entry in entries:
            assert set(entry) >= {"name", "version", "status", "content_hash", "variables"}


class TestRendering:
    def test_renders_both_halves(self, library: PromptLibrary) -> None:
        system, user = library.get("answer", "v2").render(
            question="How much is the dispute fee?",
            context="[S1] The dispute fee is 15.00 EUR.",
            refusal_marker="INSUFFICIENT_EVIDENCE",
        )
        assert "INSUFFICIENT_EVIDENCE" in system
        assert "How much is the dispute fee?" in user
        assert "15.00 EUR" in user

    def test_missing_variable_names_the_prompt_and_the_gap(self, library: PromptLibrary) -> None:
        with pytest.raises(MissingPromptVariable, match="answer.v2"):
            library.get("answer", "v2").render(question="Q")

    def test_extra_variables_are_ignored(self, library: PromptLibrary) -> None:
        system, _ = library.get("answer", "v2").render(
            question="Q", context="C", refusal_marker="R", unused="whatever"
        )
        assert system

    def test_escaped_braces_survive_rendering(self) -> None:
        # The judge prompt contains a literal JSON schema in doubled braces; it
        # must render as JSON, not be mistaken for a placeholder.
        prompt = Prompt(
            name="t", version="v1", system='Return {{"verdict": "ok"}}', user="{question}"
        )
        system, user = prompt.render(question="Q")
        assert system == 'Return {"verdict": "ok"}'
        assert user == "Q"

    def test_judge_prompt_renders_its_schema(self, library: PromptLibrary) -> None:
        system, _ = library.get("judge", "latest").render(question="Q", answer="A", context="C")
        assert '"verdict"' in system


class TestIdentity:
    def test_hash_is_stable_and_content_sensitive(self) -> None:
        first = Prompt(name="t", version="v1", system="S", user="U")
        same = Prompt(name="t", version="v1", system="S", user="U")
        different = Prompt(name="t", version="v1", system="S", user="U2")
        assert first.content_hash == same.content_hash
        assert first.content_hash != different.content_hash

    def test_version_number_is_parsed(self) -> None:
        assert Prompt(name="t", version="v12", system="", user="U").version_number == 12


class TestValidation:
    def test_rejects_a_filename_that_disagrees_with_the_body(self, tmp_path: Path) -> None:
        _write(tmp_path, "answer.v1.yaml", "name: answer\nversion: v9\nuser: 'hi'\n")
        with pytest.raises(ValueError, match="Keep them in sync"):
            PromptLibrary(tmp_path)

    def test_rejects_a_bad_filename_convention(self, tmp_path: Path) -> None:
        _write(tmp_path, "answer.yaml", "user: 'hi'\n")
        with pytest.raises(ValueError, match="convention"):
            PromptLibrary(tmp_path)

    def test_rejects_a_missing_user_template(self, tmp_path: Path) -> None:
        _write(tmp_path, "answer.v1.yaml", "name: answer\nversion: v1\nsystem: 'only system'\n")
        with pytest.raises(ValueError, match="missing required 'user'"):
            PromptLibrary(tmp_path)

    def test_rejects_undeclared_template_variables(self, tmp_path: Path) -> None:
        # Declaring `variables` is how a prompt documents its contract; drifting
        # from it is how a caller ends up passing the wrong keys.
        _write(
            tmp_path,
            "answer.v1.yaml",
            "name: answer\nversion: v1\nvariables: [question]\nuser: '{question} {context}'\n",
        )
        with pytest.raises(ValueError, match="undeclared variables"):
            PromptLibrary(tmp_path)

    def test_missing_directory_is_an_explicit_error(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="Prompt directory not found"):
            PromptLibrary(tmp_path / "absent")

    def test_empty_directory_is_an_explicit_error(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="No prompt files"):
            PromptLibrary(tmp_path)
