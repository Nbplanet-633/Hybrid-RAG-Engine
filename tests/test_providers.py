"""The adapters around optional providers: Claude, Cohere, and sentence-transformers.

Their SDKs are optional extras (not installed in CI) and real calls need models
or API keys, so each test installs a small fake module under the SDK's import
name. What's under test is this project's code: how it builds requests, parses
responses, orders scores, and reports misconfiguration.
"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import numpy as np
import pytest

from askmydocs.config import EmbeddingConfig, GenerationConfig, RerankConfig
from askmydocs.generation.llm import (
    AnthropicLLM,
    ExtractiveLLM,
    GenerationRequest,
    get_llm,
)
from askmydocs.index.embeddings import HashingEmbedder, SentenceTransformerEmbedder, get_embedder
from askmydocs.models import Chunk, RetrievedChunk
from askmydocs.rerank.rerankers import (
    CohereReranker,
    CrossEncoderReranker,
    LexicalReranker,
    get_reranker,
)


def _fake_module(monkeypatch: pytest.MonkeyPatch, name: str, **attributes: Any) -> ModuleType:
    module = ModuleType(name)
    for key, value in attributes.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)
    return module


def _candidates(*texts: str, sections: tuple[str, ...] = ()) -> list[RetrievedChunk]:
    return [
        RetrievedChunk(
            chunk=Chunk(
                chunk_id=f"c{i}",
                doc_id="doc",
                source="doc.md",
                text=text,
                section=sections[i] if i < len(sections) else "",
            ),
            score=0.0,
        )
        for i, text in enumerate(texts)
    ]


REQUEST = GenerationRequest(
    question="How long is the refund window?",
    system="Answer only from the sources.",
    user="Sources: ...\nQuestion: How long is the refund window?",
    sources=[],
    max_tokens=512,
    refusal_marker="INSUFFICIENT_EVIDENCE",
)


# ---------------------------------------------------------------------------
# Claude
# ---------------------------------------------------------------------------


class _FakeMessages:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response, self.error, self.calls = response, error, []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response

    def count_tokens(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(input_tokens=321)


def _install_anthropic(monkeypatch: pytest.MonkeyPatch, messages: _FakeMessages) -> list[dict]:
    constructed: list[dict] = []

    class Anthropic:
        def __init__(self, **kwargs: Any) -> None:
            constructed.append(kwargs)
            self.messages = messages

    _fake_module(monkeypatch, "anthropic", Anthropic=Anthropic)
    return constructed


def _response(stop_reason: str = "end_turn", content: list[Any] | None = None) -> Any:
    return SimpleNamespace(
        stop_reason=stop_reason,
        model="claude-test",
        usage=SimpleNamespace(input_tokens=600, output_tokens=40),
        content=content or [],
    )


class TestAnthropicLLM:
    def test_builds_the_request_and_joins_only_text_blocks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        messages = _FakeMessages(
            _response(
                content=[
                    SimpleNamespace(type="thinking", thinking="private reasoning"),
                    SimpleNamespace(type="text", text="You have 14 days [S1]."),
                ]
            )
        )
        constructed = _install_anthropic(monkeypatch, messages)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        llm = AnthropicLLM(model="claude-test", effort="high", timeout_s=5, max_retries=1)
        result = llm.generate(REQUEST)

        assert constructed == [{"api_key": "sk-test", "timeout": 5, "max_retries": 1}]
        call = messages.calls[0]
        assert call["model"] == "claude-test"
        assert call["system"] == REQUEST.system
        assert call["max_tokens"] == 512
        assert call["output_config"] == {"effort": "high"}
        assert call["messages"] == [{"role": "user", "content": REQUEST.user}]
        # Thinking blocks never leak into the answer.
        assert result.text == "You have 14 days [S1]."
        assert (result.input_tokens, result.output_tokens) == (600, 40)
        assert result.model == "claude-test"

    def test_a_safety_refusal_becomes_the_refusal_marker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # HTTP 200, stop_reason "refusal", empty content: must not index into it.
        _install_anthropic(monkeypatch, _FakeMessages(_response(stop_reason="refusal")))
        result = AnthropicLLM(api_key="sk-test").generate(REQUEST)
        assert result.text == "INSUFFICIENT_EVIDENCE"
        assert result.stop_reason == "refusal"

    def test_missing_credentials_get_an_actionable_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        error = TypeError("Could not resolve authentication method")
        constructed = _install_anthropic(monkeypatch, _FakeMessages(error=error))
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        llm = AnthropicLLM()
        assert "api_key" not in constructed[0]  # left for the SDK's own lookup
        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            llm.generate(REQUEST)

    def test_other_type_errors_are_not_disguised(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_anthropic(monkeypatch, _FakeMessages(error=TypeError("unexpected keyword")))
        with pytest.raises(TypeError, match="unexpected keyword"):
            AnthropicLLM(api_key="sk-test").generate(REQUEST)

    def test_exact_token_count_uses_the_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        messages = _FakeMessages()
        _install_anthropic(monkeypatch, messages)
        assert AnthropicLLM(api_key="sk-test").count_tokens_exact(REQUEST) == 321
        assert messages.calls[0]["system"] == REQUEST.system


def test_llm_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    assert isinstance(get_llm(GenerationConfig(provider="extractive")), ExtractiveLLM)
    _install_anthropic(monkeypatch, _FakeMessages())
    assert isinstance(get_llm(GenerationConfig(provider="anthropic")), AnthropicLLM)
    with pytest.raises(ValueError, match="gpt"):
        get_llm(GenerationConfig.model_construct(provider="gpt"))


# ---------------------------------------------------------------------------
# Rerankers
# ---------------------------------------------------------------------------


class TestCrossEncoderReranker:
    def test_scores_section_and_text_together_and_orders_best_first(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}

        class CrossEncoder:
            def __init__(self, model: str, device: str | None = None) -> None:
                seen["model"] = model

            def predict(self, pairs: list[tuple[str, str]], **kwargs: Any) -> list[float]:
                seen["pairs"], seen["kwargs"] = pairs, kwargs
                return [0.1, 2.5, -1.0]

        _fake_module(monkeypatch, "sentence_transformers", CrossEncoder=CrossEncoder)
        reranker = CrossEncoderReranker(model="ce-test", batch_size=8)
        candidates = _candidates("fees", "refund window", "webhooks", sections=("Fees", "Refunds"))

        ranked = reranker.rerank("refund window?", candidates, top_n=2)

        assert seen["model"] == "ce-test"
        assert seen["pairs"][1] == ("refund window?", "Refunds\nrefund window")
        assert seen["pairs"][2] == ("refund window?", "webhooks")  # no section: text only
        assert seen["kwargs"]["batch_size"] == 8
        assert [c.chunk.chunk_id for c in ranked] == ["c1", "c0"]
        assert ranked[0].rerank_score == 2.5

    def test_no_candidates_skips_the_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class CrossEncoder:
            def __init__(self, *args: Any, **kwargs: Any) -> None: ...

            def predict(self, *args: Any, **kwargs: Any) -> list[float]:
                raise AssertionError("must not be called")

        _fake_module(monkeypatch, "sentence_transformers", CrossEncoder=CrossEncoder)
        assert CrossEncoderReranker().rerank("q", [], top_n=5) == []


class TestCohereReranker:
    def test_maps_results_back_to_candidates_by_index(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[dict] = []

        class ClientV2:
            def __init__(self, api_key: str) -> None:
                self.api_key = api_key

            def rerank(self, **kwargs: Any) -> Any:
                calls.append(kwargs)
                return SimpleNamespace(
                    results=[
                        SimpleNamespace(index=2, relevance_score=0.93),
                        SimpleNamespace(index=0, relevance_score=0.41),
                    ]
                )

        _fake_module(monkeypatch, "cohere", ClientV2=ClientV2)
        monkeypatch.setenv("COHERE_API_KEY", "co-test")
        candidates = _candidates("a", "b", "c")

        ranked = CohereReranker(model="rerank-test").rerank("q", candidates, top_n=10)

        # top_n is capped at the number of documents sent.
        assert calls[0] == {
            "model": "rerank-test",
            "query": "q",
            "documents": ["a", "b", "c"],
            "top_n": 3,
        }
        assert [(c.chunk.chunk_id, c.score) for c in ranked] == [("c2", 0.93), ("c0", 0.41)]

    def test_requires_an_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _fake_module(monkeypatch, "cohere", ClientV2=object)
        monkeypatch.delenv("COHERE_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="COHERE_API_KEY"):
            CohereReranker()

    def test_no_candidates_skips_the_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _fake_module(monkeypatch, "cohere", ClientV2=lambda api_key: None)
        assert CohereReranker(api_key="co-test").rerank("q", [], top_n=3) == []


def test_reranker_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    assert isinstance(get_reranker(RerankConfig(provider="lexical")), LexicalReranker)
    _fake_module(monkeypatch, "sentence_transformers", CrossEncoder=lambda model, device=None: None)
    assert isinstance(get_reranker(RerankConfig(provider="cross-encoder")), CrossEncoderReranker)
    _fake_module(monkeypatch, "cohere", ClientV2=lambda api_key: None)
    monkeypatch.setenv("COHERE_API_KEY", "co-test")
    assert isinstance(get_reranker(RerankConfig(provider="cohere")), CohereReranker)
    with pytest.raises(ValueError, match="colbert"):
        get_reranker(RerankConfig.model_construct(provider="colbert"))


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------


def _install_sentence_transformer(
    monkeypatch: pytest.MonkeyPatch, *, new_api: bool = True, dimension: int | None = 4
) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    class SentenceTransformer:
        def __init__(self, model: str, device: str | None = None) -> None:
            seen["model"] = model

        def encode(self, items: list[str], **kwargs: Any) -> Any:
            seen["kwargs"] = kwargs
            return [[float(len(text)), 1.0, 0.0, 0.0] for text in items]

    if new_api:
        SentenceTransformer.get_embedding_dimension = lambda self: dimension  # type: ignore[attr-defined]
    else:
        SentenceTransformer.get_sentence_embedding_dimension = lambda self: dimension  # type: ignore[attr-defined]
    _fake_module(monkeypatch, "sentence_transformers", SentenceTransformer=SentenceTransformer)
    return seen


class TestSentenceTransformerEmbedder:
    @pytest.mark.parametrize("new_api", [True, False])
    def test_reads_the_dimension_from_either_sdk_api(
        self, monkeypatch: pytest.MonkeyPatch, new_api: bool
    ) -> None:
        # sentence-transformers 6 renamed the method; both must keep working.
        _install_sentence_transformer(monkeypatch, new_api=new_api)
        embedder = SentenceTransformerEmbedder(model="st-test")
        assert embedder.dimension == 4
        assert embedder.fingerprint == "sentence-transformers:st-test:4"

    def test_a_model_without_a_dimension_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_sentence_transformer(monkeypatch, dimension=None)
        with pytest.raises(RuntimeError, match="dimension"):
            SentenceTransformerEmbedder(model="st-test")

    def test_embeds_as_float32_with_the_configured_options(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = _install_sentence_transformer(monkeypatch)
        embedder = SentenceTransformerEmbedder(batch_size=7, normalize_vectors=False)

        matrix = embedder.embed_documents(["ab", "abcd"])
        query = embedder.embed_query("abc")

        assert matrix.dtype == np.float32 and matrix.shape == (2, 4)
        assert query.dtype == np.float32 and query.shape == (4,)
        assert query[0] == 3.0
        assert seen["kwargs"]["batch_size"] == 7
        assert seen["kwargs"]["normalize_embeddings"] is False

    def test_no_texts_gives_an_empty_matrix_without_calling_the_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = _install_sentence_transformer(monkeypatch)
        empty = SentenceTransformerEmbedder().embed_documents([])
        assert empty.shape == (0, 4)
        assert "kwargs" not in seen


def test_embedder_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    assert isinstance(get_embedder(EmbeddingConfig(provider="hashing")), HashingEmbedder)
    _install_sentence_transformer(monkeypatch)
    embedder = get_embedder(EmbeddingConfig(provider="sentence-transformers"))
    assert isinstance(embedder, SentenceTransformerEmbedder)
    with pytest.raises(ValueError, match="word2vec"):
        get_embedder(EmbeddingConfig.model_construct(provider="word2vec"))
