"""LLM providers for the answer-generation step.

Two providers behind one protocol:

* ``AnthropicLLM``  — Claude via the Messages API. Production default.
* ``ExtractiveLLM`` — a deterministic extractive generator that selects the
  best-supporting sentences from the retrieved passages and emits them with
  citation markers. No API key, no network, byte-identical output run to run.

``ExtractiveLLM`` is not a toy stub. It makes the whole pipeline — retrieval,
fusion, reranking, citation enforcement, abstention, and the evaluation gate —
runnable in CI on every pull request, which is what turns the eval suite into a
real regression gate rather than a manual script somebody runs occasionally.

Notes on the Claude request shape (Opus 5 / Sonnet 5 family):

* ``temperature`` / ``top_p`` / ``top_k`` are **rejected** with a 400 — output is
  steered by the prompt, not by sampling parameters.
* Thinking is **on by default**; depth is controlled by ``output_config.effort``.
  We leave it on at ``medium`` rather than disabling it, because disabling
  thinking on this family can leak reasoning into the visible response.
* ``max_tokens`` is a hard cap on thinking **plus** answer text, so it is sized
  with headroom above the expected answer length.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from askmydocs.text import lexical_tokens, split_sentences, topical_tokens


@dataclass
class SourceBlock:
    """One numbered passage handed to the generator."""

    marker: str  # "S1"
    chunk_id: str
    source: str
    section: str
    text: str
    score: float = 0.0


@dataclass
class GenerationRequest:
    """Everything a provider needs to produce an answer.

    Carries both the rendered prompt strings (used by hosted models) and the
    structured passages (used by the extractive provider). A provider ignores
    whichever half it does not need.
    """

    question: str
    system: str
    user: str
    sources: list[SourceBlock]
    max_tokens: int = 2048
    refusal_marker: str = "INSUFFICIENT_EVIDENCE"


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class LLM(Protocol):
    name: str
    model: str

    def generate(self, request: GenerationRequest) -> LLMResponse: ...


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------


class AnthropicLLM:
    """Claude via the Anthropic Messages API."""

    name = "anthropic"

    def __init__(
        self,
        model: str = "claude-opus-5",
        effort: str = "medium",
        timeout_s: float = 60.0,
        api_key: str | None = None,
        max_retries: int = 3,
    ) -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "The Anthropic provider requires an extra install:\n"
                "    pip install 'ask-my-docs[anthropic]'\n"
                "Or switch to the offline profile: ASKMYDOCS_PROFILE=offline"
            ) from exc

        key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not key:
            # The SDK can also resolve an `ant auth login` profile, so an unset
            # env var is not necessarily fatal — construct and let it try.
            key = None

        self.model = model
        self.effort = effort
        self._client = anthropic.Anthropic(
            **({"api_key": key} if key else {}),
            timeout=timeout_s,
            max_retries=max_retries,
        )

    def generate(self, request: GenerationRequest) -> LLMResponse:
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=request.max_tokens,
                system=request.system,
                # Effort controls thinking depth and total token spend. Thinking is
                # on by default on this model family; no sampling parameters are
                # allowed.
                output_config={"effort": self.effort},
                messages=[{"role": "user", "content": request.user}],
            )
        except TypeError as exc:
            # The SDK raises a bare TypeError when it cannot resolve credentials.
            # Credentials are only checked at request time, because the SDK can
            # also resolve an `ant auth login` profile with no env var set — so
            # this is where a missing key surfaces, and it needs to say what to do.
            if "authentication" in str(exc).lower():
                raise RuntimeError(
                    "No Anthropic credentials found. Either export ANTHROPIC_API_KEY "
                    "(see .env.example), run `ant auth login`, or switch to a profile "
                    "that needs no key: ASKMYDOCS_PROFILE=offline (or full-retrieval "
                    "for the real retrieval stack with a local generator)."
                ) from exc
            raise

        # A safety classifier can decline the request: HTTP 200 with
        # stop_reason "refusal" and an empty content list. Check before indexing.
        if response.stop_reason == "refusal":
            return LLMResponse(
                text=request.refusal_marker,
                model=response.model,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                stop_reason="refusal",
            )

        text = "\n".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        ).strip()

        return LLMResponse(
            text=text,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            stop_reason=response.stop_reason or "",
        )

    def count_tokens_exact(self, request: GenerationRequest) -> int:
        """Exact input token count for a request, via the count_tokens endpoint.

        This is the correct way to measure Claude tokens. The local approximation
        in :mod:`askmydocs.text` exists for chunking, where thousands of counts
        per document rule out a network round trip.
        """
        result = self._client.messages.count_tokens(
            model=self.model,
            system=request.system,
            messages=[{"role": "user", "content": request.user}],
        )
        return int(result.input_tokens)


# ---------------------------------------------------------------------------
# Extractive (deterministic, offline)
# ---------------------------------------------------------------------------

# Question forms whose answer is almost always a number, an amount, or a window.
_NUMERIC_QUESTION_RE = re.compile(
    r"\b(how (long|many|much|often)|what (is|are) the (fee|cost|price|limit|minimum|maximum|rate)"
    r"|minimum|maximum|limit|deadline|window|threshold|percentage|percent|ratio|how big)\b",
    re.IGNORECASE,
)
_DIGIT_RE = re.compile(r"\d")
# Leading markdown list marker, stripped so extracted text reads as prose.
_LIST_MARKER_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
# Already ends a sentence: terminator, optionally inside closing emphasis or
# quotes ("evidence.**"). Without this, punctuation repair appends a second stop.
_ENDS_SENTENCE_RE = re.compile(r"[.!?][\"'*_)\]]*$")


class ExtractiveLLM:
    """Deterministic extractive generator.

    Selects the sentences from the retrieved passages that best cover the
    question's content terms, using greedy maximal marginal coverage so a
    multi-part question draws on more than one passage, and emits them with the
    citation markers the enforcement layer expects.
    """

    name = "extractive"

    def __init__(
        self,
        model: str = "extractive-v1",
        max_sentences: int = 3,
        min_relevance: float = 0.12,
        marginal_ratio: float = 0.65,
    ) -> None:
        self.model = model
        self.max_sentences = max_sentences
        self.min_relevance = min_relevance
        self.marginal_ratio = marginal_ratio

    # -- scoring -----------------------------------------------------------

    @staticmethod
    def _coverage(sentence_tokens: set[str], query_tokens: set[str]) -> float:
        if not query_tokens:
            return 0.0
        return len(sentence_tokens & query_tokens) / len(query_tokens)

    def _sentence_score(self, sentence: str, query_tokens: set[str], wants_number: bool) -> float:
        tokens = lexical_tokens(sentence)
        if not tokens:
            return 0.0
        token_set = set(tokens)
        coverage = self._coverage(token_set, query_tokens)
        # Density keeps a long, vaguely-related sentence from outranking a short,
        # precisely-on-point one.
        density = sum(1 for t in tokens if t in query_tokens) / (len(tokens) ** 0.5)
        score = 0.7 * coverage + 0.3 * min(density, 1.0)
        if wants_number and _DIGIT_RE.search(sentence):
            score += 0.15
        # Very short fragments are rarely self-contained answers.
        if len(tokens) < 4:
            score *= 0.5
        return score

    # -- generation --------------------------------------------------------

    def generate(self, request: GenerationRequest) -> LLMResponse:
        # Scaffolding words ('long', 'many') would otherwise let an unrelated
        # sentence score for containing 'long fulfilment horizons'.
        query_tokens = set(topical_tokens(request.question))
        wants_number = bool(_NUMERIC_QUESTION_RE.search(request.question))

        # Score every sentence in every passage once.
        candidates: list[tuple[float, SourceBlock, str, set[str]]] = []
        for block in request.sources:
            for sentence in split_sentences(block.text):
                cleaned = sentence.strip()
                # Headings, table rows, and code lines are indexed on purpose --
                # they carry retrieval signal -- but they read badly as answer
                # prose, so they are not eligible for extraction.
                if not cleaned or cleaned.startswith(("|", "```", "~~~", "#")):
                    continue
                # Strip a leading list marker so the answer reads as a sentence.
                cleaned = _LIST_MARKER_RE.sub("", cleaned)
                if not cleaned:
                    continue
                tokens = set(lexical_tokens(cleaned))
                score = self._sentence_score(cleaned, query_tokens, wants_number)
                if score > 0:
                    candidates.append((score, block, cleaned, tokens))

        if not candidates:
            return LLMResponse(text=request.refusal_marker, model=self.model, stop_reason="abstain")

        candidates.sort(key=lambda item: item[0], reverse=True)
        best_score = candidates[0][0]
        if best_score < self.min_relevance:
            # Nothing retrieved is actually about the question.
            return LLMResponse(text=request.refusal_marker, model=self.model, stop_reason="abstain")

        # Greedy maximal marginal coverage: each additional sentence must cover
        # query terms the selection does not already cover.
        selected: list[tuple[SourceBlock, str]] = []
        covered: set[str] = set()
        used_sources: set[str] = set()

        # An additional sentence must clear a floor relative to the best one. A
        # sentence that merely shares an incidental word makes the answer worse,
        # not more complete — this floor is what keeps the answer on topic.
        floor = max(self.min_relevance, best_score * self.marginal_ratio)

        for score, block, sentence, tokens in candidates:
            if len(selected) >= self.max_sentences:
                break
            if not selected:
                selected.append((block, sentence))
                covered |= tokens & query_tokens
                used_sources.add(block.marker)
                continue
            if score < floor:
                # Candidates are score-sorted, so nothing below this qualifies.
                break
            gain = len((tokens & query_tokens) - covered)
            # Require genuinely new information, and prefer breadth across passages.
            if gain == 0:
                continue
            if block.marker in used_sources and gain < 2:
                continue
            selected.append((block, sentence))
            covered |= tokens & query_tokens
            used_sources.add(block.marker)

        # Emit in passage order so the answer reads coherently.
        order = {block.marker: index for index, block in enumerate(request.sources)}
        selected.sort(key=lambda pair: order.get(pair[0].marker, 0))

        parts: list[str] = []
        for block, sentence in selected:
            body = sentence.rstrip()
            # A trailing colon or semicolon introduces something that is not here.
            if body.endswith((":", ";")):
                body = f"{body[:-1]}."
            elif not _ENDS_SENTENCE_RE.search(body):
                body = f"{body}."
            parts.append(f"{body} [{block.marker}]")

        text = " ".join(parts).strip()
        return LLMResponse(
            text=text,
            model=self.model,
            input_tokens=sum(len(lexical_tokens(b.text)) for b in request.sources),
            output_tokens=len(lexical_tokens(text)),
            stop_reason="end_turn",
        )


def get_llm(config) -> LLM:
    """Construct the LLM described by a :class:`GenerationConfig`."""
    if config.provider == "extractive":
        return ExtractiveLLM(model=config.model)
    if config.provider == "anthropic":
        return AnthropicLLM(
            model=config.model,
            effort=config.effort,
            timeout_s=config.timeout_s,
        )
    raise ValueError(f"Unknown generation provider: {config.provider!r}")
