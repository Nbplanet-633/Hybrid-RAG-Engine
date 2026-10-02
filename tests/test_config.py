"""Profile requirement checks: what each shipped profile needs before it can start.

``find_spec`` and the API-key variables are faked, so these assert the same thing
on a bare CI runner and on a machine with every optional extra installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from askmydocs import config as config_module
from askmydocs.config import load_config, missing_requirements

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_YAML = REPO_ROOT / "config" / "app.yaml"


@pytest.fixture
def installed(monkeypatch: pytest.MonkeyPatch):
    """Control which optional packages appear installed, and clear the API keys."""
    present: set[str] = set()
    monkeypatch.setattr(
        config_module.importlib.util,
        "find_spec",
        lambda name: object() if name in present else None,
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("COHERE_API_KEY", raising=False)
    return present


def _missing(profile: str) -> list[str]:
    return missing_requirements(load_config(path=APP_YAML, profile=profile))


def test_offline_needs_nothing(installed: set[str]) -> None:
    assert _missing("offline") == []


def test_full_retrieval_names_the_packages_and_extras_to_install(installed: set[str]) -> None:
    missing = _missing("full-retrieval")
    assert any("sentence_transformers" in m and '".[models]"' in m for m in missing)
    assert any("chromadb" in m and '".[chroma]"' in m for m in missing)
    # No generation model, so no API key.
    assert not any("API_KEY" in m for m in missing)


def test_full_retrieval_is_ready_once_the_packages_are_installed(installed: set[str]) -> None:
    installed |= {"sentence_transformers", "chromadb"}
    assert _missing("full-retrieval") == []


def test_full_also_needs_claude_and_its_key(
    installed: set[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    installed |= {"sentence_transformers", "chromadb"}
    assert _missing("full") == [
        'the `anthropic` package (pip install -e ".[anthropic]")',
        "the ANTHROPIC_API_KEY environment variable",
    ]

    installed.add("anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert _missing("full") == []


def test_a_disabled_reranker_needs_no_reranker_package(installed: set[str]) -> None:
    cfg = load_config(
        path=APP_YAML,
        profile="full-retrieval",
        overrides={"rerank": {"enabled": False}, "embedding": {"provider": "hashing"}},
    )
    assert not any("sentence_transformers" in m for m in missing_requirements(cfg))
