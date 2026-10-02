"""Answer generation with enforced citations and explicit abstention.

This module is the trust boundary of the system. A RAG demo stops at "retrieve,
stuff, generate"; the difference in production is that the generated text is not
returned until it has passed a set of checks, and the system is willing to return
nothing at all.

Three gates, in order:

1. **Pre-generation relevance gate.** If no reranked candidate clears
   ``citations.min_top_score``, or fewer than ``min_sources`` do, we abstain
   *without calling the model*. Nothing good comes from asking an LLM to answer
   from passages that are off-topic — it saves latency and cost, and removes the
   most common hallucination trigger entirely.

2. **Refusal sentinel.** The prompt instructs the model to emit a machine-readable
   sentinel when the passages are insufficient. Detecting a token is reliable;
   pattern-matching hedged prose ("I'm not certain, but...") is not.

3. **Post-generation citation and grounding validation.** Every ``[S#]`` marker
   must resolve to a passage that was actually supplied — invented markers are
   stripped and logged. The answer must carry at least one valid citation. And the
   answer's content words must overlap the cited passages by at least
   ``citations.min_grounding``; a fluent answer with no lexical footprint in its
   own sources is the signature of fabrication.

Failing any gate produces an :class:`~askmydocs.models.Answer` with
``abstained=True`` and a machine-readable ``abstain_reason``, never a silent
best-effort guess.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable

from askmydocs.config import CitationConfig, GenerationConfig
from askmydocs.generation.llm import LLM, GenerationRequest, SourceBlock
from askmydocs.generation.prompts import PromptLibrary
from askmydocs.models import Answer, Citation, RetrievedChunk, TokenUsage
from askmydocs.text import (
    best_sentence,
    overlap_ratio,
    split_sentences,
    topical_tokens,
    truncate,
)

_MARKER_RE = re.compile(r"\[\s*S(\d+)\s*\]", re.IGNORECASE)

# Reasons are stable identifiers so dashboards and the eval harness can group on
# them without parsing prose.
REASON_NO_RESULTS = "no_results"
REASON_LOW_RELEVANCE = "low_relevance"
REASON_MODEL_REFUSAL = "model_refusal"
REASON_NO_CITATIONS = "no_citations"
REASON_LOW_GROUNDING = "low_grounding"
REASON_EMPTY_RESPONSE = "empty_response"
REASON_QUESTION_NOT_COVERED = "question_not_covered"


def claims_by_marker(text: str) -> dict[str, str]:
    """Map each ``S#`` marker to the answer prose it is cited for, markers stripped.

    A marker supports the sentence it sits in. One that ends up alone after
    sentence splitting ("...evidence.** [S1]" splits after the period) belongs to
    the sentence before it.
    """
    claims: dict[str, list[str]] = {}
    previous = ""
    for sentence in split_sentences(text):
        prose = _MARKER_RE.sub("", sentence).strip()
        claim = prose or previous
        for match in _MARKER_RE.finditer(sentence):
            claims.setdefault(f"S{match.group(1)}", []).append(claim)
        if prose:
            previous = prose
    return {marker: " ".join(parts) for marker, parts in claims.items()}


class Answerer:
    def __init__(
        self,
        llm: LLM,
        prompts: PromptLibrary,
        generation: GenerationConfig,
        citations: CitationConfig,
    ) -> None:
        self.llm = llm
        self.prompts = prompts
        self.generation = generation
        self.citations = citations
        # Resolve the prompt once at construction so a bad version fails fast at
        # startup rather than on the first user request.
        self.prompt = prompts.get(generation.prompt, generation.prompt_version)
        self._idf_provider: Callable[[], dict[str, float]] | None = None
        self._max_idf_provider: Callable[[], float] | None = None

    def set_idf_provider(
        self,
        idf_provider: Callable[[], dict[str, float]],
        max_idf_provider: Callable[[], float] | None = None,
    ) -> None:
        """Attach corpus IDF statistics, used by the question-coverage gate."""
        self._idf_provider = idf_provider
        self._max_idf_provider = max_idf_provider

    def question_coverage(self, question: str) -> float:
        """IDF-weighted fraction of the question's topical terms the corpus contains.

        The intuition: a question the corpus cannot answer usually contains at
        least one pivotal term the corpus never uses. Weighting by IDF makes that
        term dominate — a term absent from the corpus is by definition maximally
        rare — while corpus-wide filler contributes almost nothing. So "What is
        Aurora's parental leave policy?" scores low even though "Aurora" and
        "policy" appear on every page.

        Deliberately measured against the **corpus vocabulary**, not the retrieved
        passages. Those are different failure modes and deserve different reasons:
        a term the corpus never uses means the question is out of scope, whereas a
        term the corpus has but retrieval missed is a recall problem, which shows
        up as ``low_relevance`` or ``no_citations`` instead. Conflating them makes
        every retrieval miss look like an out-of-scope question, which is both
        wrong and much harder to debug.

        Returns 1.0 when no IDF statistics are available, so the gate degrades to
        inert rather than silently rejecting everything.
        """
        terms = set(topical_tokens(question))
        if not terms:
            return 1.0
        idf = self._idf_provider() if self._idf_provider else {}
        if not idf:
            return 1.0
        default = self._max_idf_provider() if self._max_idf_provider else max(idf.values())
        weights = {term: max(idf.get(term, default), 0.01) for term in terms}
        total = sum(weights.values())
        if total <= 0:
            return 1.0
        in_corpus = sum(weights[term] for term in terms if term in idf)
        return round(in_corpus / total, 4)

    # -- context assembly --------------------------------------------------

    def _build_sources(self, candidates: list[RetrievedChunk]) -> list[SourceBlock]:
        blocks: list[SourceBlock] = []
        budget = self.generation.max_context_chars
        for index, candidate in enumerate(candidates, start=1):
            chunk = candidate.chunk
            body = truncate(chunk.text, self.generation.max_chars_per_source)
            if budget - len(body) < 0 and blocks:
                # Keep the context inside its budget rather than silently relying
                # on the model's context window to absorb it.
                break
            budget -= len(body)
            blocks.append(
                SourceBlock(
                    marker=f"S{index}",
                    chunk_id=chunk.chunk_id,
                    source=chunk.source,
                    section=chunk.section,
                    text=body,
                    score=candidate.score,
                )
            )
        return blocks

    @staticmethod
    def _render_context(blocks: list[SourceBlock]) -> str:
        parts: list[str] = []
        for block in blocks:
            header = f"[{block.marker}] source: {block.source}"
            if block.section:
                header += f" | section: {block.section}"
            parts.append(f"{header}\n{block.text}")
        return "\n\n".join(parts)

    # -- outcome helpers ---------------------------------------------------

    def _abstain(
        self,
        question: str,
        reason: str,
        retrieved: list[RetrievedChunk],
        started: float,
        usage: TokenUsage | None = None,
        grounding: float = 0.0,
    ) -> Answer:
        return Answer(
            question=question,
            text=self.citations.refusal_message,
            citations=[],
            abstained=True,
            abstain_reason=reason,
            confidence=0.0,
            grounding_score=grounding,
            retrieved=retrieved,
            prompt_name=self.prompt.name,
            prompt_version=self.prompt.version,
            model=getattr(self.llm, "model", ""),
            usage=usage or TokenUsage(),
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    @staticmethod
    def _confidence(
        grounding: float, citations: list[Citation], cited: list[RetrievedChunk]
    ) -> float:
        """Bounded confidence built only from signals we actually measured.

        * grounding          — lexical support of the answer in its own sources
        * citation breadth   — one supporting passage is weaker than two
        * retriever agreement — passages found by *both* dense and BM25 legs are
                                stronger evidence than single-leg hits
        """
        breadth = min(len(citations) / 2.0, 1.0)
        agreement = (
            sum(1 for item in cited if item.retriever == "hybrid") / len(cited) if cited else 0.0
        )
        return round(min(1.0, 0.50 * grounding + 0.25 * breadth + 0.25 * agreement), 4)

    # -- main entry point --------------------------------------------------

    def answer(self, question: str, retrieved: list[RetrievedChunk]) -> Answer:
        started = time.perf_counter()

        # --- Gate 1: pre-generation relevance ----------------------------
        if not retrieved:
            return self._abstain(question, REASON_NO_RESULTS, [], started)

        eligible = [c for c in retrieved if c.score >= self.citations.min_top_score]
        if len(eligible) < self.citations.min_sources:
            return self._abstain(question, REASON_LOW_RELEVANCE, retrieved, started)

        # --- Gate 1b: is the question even about something the corpus covers?
        if (
            self.citations.min_question_coverage > 0.0
            and self.question_coverage(question) < self.citations.min_question_coverage
        ):
            return self._abstain(question, REASON_QUESTION_NOT_COVERED, retrieved, started)

        blocks = self._build_sources(eligible)
        if not blocks:
            return self._abstain(question, REASON_NO_RESULTS, retrieved, started)

        context = self._render_context(blocks)

        system, user = self.prompt.render(
            question=question,
            context=context,
            refusal_marker=self.citations.refusal_marker,
        )

        response = self.llm.generate(
            GenerationRequest(
                question=question,
                system=system,
                user=user,
                sources=blocks,
                max_tokens=self.generation.max_tokens,
                refusal_marker=self.citations.refusal_marker,
            )
        )
        usage = TokenUsage(input_tokens=response.input_tokens, output_tokens=response.output_tokens)
        text = (response.text or "").strip()

        # --- Gate 2: refusal sentinel ------------------------------------
        if not text:
            return self._abstain(question, REASON_EMPTY_RESPONSE, retrieved, started, usage)
        if self.citations.refusal_marker in text:
            return self._abstain(question, REASON_MODEL_REFUSAL, retrieved, started, usage)

        # --- Gate 3: citation validation ---------------------------------
        by_marker = {block.marker.upper(): block for block in blocks}
        used_markers: list[str] = []
        invalid_markers: list[str] = []

        for match in _MARKER_RE.finditer(text):
            marker = f"S{match.group(1)}"
            if marker.upper() in by_marker:
                if marker not in used_markers:
                    used_markers.append(marker)
            elif marker not in invalid_markers:
                invalid_markers.append(marker)

        if invalid_markers:
            # The model referenced a passage that was never supplied. Remove the
            # marker rather than returning a citation a reader cannot follow.
            for marker in invalid_markers:
                text = re.sub(rf"\[\s*{re.escape(marker)}\s*\]", "", text, flags=re.IGNORECASE)
            text = re.sub(r"[ \t]{2,}", " ", text).strip()

        if self.citations.require_citations and not used_markers:
            return self._abstain(question, REASON_NO_CITATIONS, retrieved, started, usage)

        cited_blocks = [by_marker[m.upper()] for m in used_markers]
        cited_ids = {block.chunk_id for block in cited_blocks}
        cited_retrieved = [c for c in eligible if c.chunk.chunk_id in cited_ids]

        # --- Gate 3b: grounding ------------------------------------------
        # Compare the answer prose (markers stripped) against the cited passages.
        answer_prose = _MARKER_RE.sub("", text)
        supporting_text = "\n".join(block.text for block in cited_blocks) or "\n".join(
            block.text for block in blocks
        )
        grounding = round(overlap_ratio(answer_prose, supporting_text), 4)

        if grounding < self.citations.min_grounding:
            return self._abstain(
                question, REASON_LOW_GROUNDING, retrieved, started, usage, grounding
            )

        # Quote the passage sentence closest to the claim each source is cited for,
        # not to the question: a passage can answer several questions, and the
        # quote has to show the evidence for *this* answer. For a quoted answer
        # that is the identical sentence; for a generated one, its best support.
        claims = claims_by_marker(text)
        citations = [
            Citation(
                marker=block.marker,
                chunk_id=block.chunk_id,
                source=block.source,
                title=next(
                    (c.chunk.title for c in eligible if c.chunk.chunk_id == block.chunk_id), ""
                ),
                section=block.section,
                quote=truncate(
                    best_sentence(claims.get(block.marker) or question, block.text), 320
                ),
                score=block.score,
            )
            for block in cited_blocks
        ]

        return Answer(
            question=question,
            text=text,
            citations=citations,
            abstained=False,
            abstain_reason="",
            confidence=self._confidence(grounding, citations, cited_retrieved),
            grounding_score=grounding,
            retrieved=retrieved,
            prompt_name=self.prompt.name,
            prompt_version=self.prompt.version,
            model=response.model or getattr(self.llm, "model", ""),
            usage=usage,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
