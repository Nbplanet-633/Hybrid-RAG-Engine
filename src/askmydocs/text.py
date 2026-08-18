"""Shared text utilities: tokenisation, sentence splitting, and lexical overlap.

On token counting
-----------------
Chunking needs a token count for *every* candidate boundary — thousands of calls
per document — so it must be local, fast, and deterministic. This module uses a
subword-aware approximation (~4 characters per token, the well-known English
heuristic, refined per word class) rather than a model tokeniser.

That is deliberate, and it is an approximation. When you need an exact count for
a Claude request (cost estimation, context-budget checks) use the Messages API's
``count_tokens`` endpoint via :func:`askmydocs.generation.llm.count_tokens_exact`
— never ``tiktoken``, which is OpenAI's tokeniser and undercounts Claude tokens.
The approximation here is calibrated to run slightly *high*, so chunks stay
inside the configured budget rather than overflowing it.
"""

from __future__ import annotations

import math
import re
import unicodedata

# Word-ish atoms, keeping punctuation separate so counts track real tokenisers.
_ATOM_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'\-]*", re.UNICODE)

# Sentence boundary: a terminator, optionally followed by closing markdown
# emphasis or quote characters, then whitespace, then the start of a new sentence.
#
# The emphasis alternatives matter more than they look. Markdown puts the period
# *inside* the bold span -- "**...to submit evidence.** This is shorter..." -- so a
# plain `(?<=[.!?])\s+` lookbehind sees `*` before the space and never splits.
# The two sentences then merge into one long span, which dilutes every
# density-based relevance score and makes the wrong sentence win extraction.
#
# Python requires fixed-width lookbehinds, so the variants are spelled out as an
# alternation of three fixed widths rather than a single `{0,2}` quantifier.
_SENTENCE_END_RE = re.compile(
    r"(?:(?<=[.!?])|(?<=[.!?][\"'*_)\]])|(?<=[.!?][\"'*_)\]]{2}))"
    r"\s+(?=[\"'(\[*_]*[A-Z0-9])"
)
_ABBREVIATIONS = {
    "e.g.",
    "i.e.",
    "etc.",
    "vs.",
    "no.",
    "fig.",
    "approx.",
    "inc.",
    "ltd.",
    "mr.",
    "mrs.",
    "ms.",
    "dr.",
    "st.",
    "u.s.",
    "u.k.",
    "a.m.",
    "p.m.",
}

STOPWORDS = frozenset(
    [
        "a",
        "about",
        "above",
        "after",
        "again",
        "against",
        "all",
        "am",
        "an",
        "and",
        "any",
        "are",
        "aren't",
        "as",
        "at",
        "be",
        "because",
        "been",
        "before",
        "being",
        "below",
        "between",
        "both",
        "but",
        "by",
        "can",
        "cannot",
        "could",
        "couldn't",
        "did",
        "didn't",
        "do",
        "does",
        "doesn't",
        "doing",
        "don't",
        "down",
        "during",
        "each",
        "few",
        "for",
        "from",
        "further",
        "had",
        "hadn't",
        "has",
        "hasn't",
        "have",
        "haven't",
        "having",
        "he",
        "her",
        "here",
        "hers",
        "herself",
        "him",
        "himself",
        "his",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "isn't",
        "it",
        "its",
        "itself",
        "let's",
        "me",
        "more",
        "most",
        "mustn't",
        "my",
        "myself",
        "no",
        "nor",
        "not",
        "of",
        "off",
        "on",
        "once",
        "only",
        "or",
        "other",
        "ought",
        "our",
        "ours",
        "ourselves",
        "out",
        "over",
        "own",
        "same",
        "shan't",
        "she",
        "should",
        "shouldn't",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "theirs",
        "them",
        "themselves",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "to",
        "too",
        "under",
        "until",
        "up",
        "very",
        "was",
        "wasn't",
        "we",
        "were",
        "weren't",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "whom",
        "why",
        "with",
        "won't",
        "would",
        "wouldn't",
        "you",
        "your",
        "yours",
        "yourself",
        "yourselves",
    ]
)


# Words that carry no topical content when they appear in a *question*.
#
# Used ONLY by the question-coverage abstention gate, never by BM25 indexing or
# scoring. The gate treats a query term absent from the corpus as strong evidence
# the question is out of scope — but "How *often* are keys rotated?" contains an
# absent word that says nothing about the topic. Removing this set is what lets
# the gate distinguish "the corpus never discusses Salesforce" from "the corpus
# never happens to use the word 'often'".
#
# Kept deliberately small and free of domain nouns: adding a topical word here
# would blind the gate to exactly the questions it exists to reject.
QUESTION_SCAFFOLDING = frozenset(
    [
        "often",
        "many",
        "much",
        "long",
        "far",
        "big",
        "small",
        "need",
        "needs",
        "needed",
        "want",
        "wants",
        "tell",
        "explain",
        "describe",
        "mean",
        "means",
        "meaning",
        "list",
        "show",
        "shows",
        "give",
        "gives",
        "make",
        "makes",
        "made",
        "use",
        "uses",
        "used",
        "using",
        "work",
        "works",
        "working",
        "happen",
        "happens",
        "occur",
        "occurs",
        "allowed",
        "allow",
        "possible",
        "available",
        "whether",
        "either",
        "neither",
        "instead",
        "rather",
        "must",
        "may",
        "might",
        "shall",
        "will",
        "please",
        "kind",
        "sort",
        "thing",
        "things",
        "way",
        "ways",
        "able",
        "let",
        "us",
        "know",
        "like",
        "just",
        "really",
        "actually",
    ]
)


def normalize(text: str) -> str:
    """NFKC-normalise, collapse whitespace, and lowercase."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip().lower()


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens, for BM25 and lexical overlap scoring."""
    return _WORD_RE.findall(normalize(text))


def content_tokens(text: str) -> list[str]:
    """Word tokens with stopwords and 1-character tokens removed."""
    return [t for t in tokenize(text) if t not in STOPWORDS and len(t) > 1]


# Ordered suffix rules for the lightweight stemmer below. Longest-match-first.
# The replacement need not be a real word — only *consistent*, so that every
# surface form of a term collapses to the same key on both sides of a comparison.
_SUFFIX_RULES: tuple[tuple[str, str], ...] = (
    ("ational", "at"),
    # "-ption" <-> "-pt" ("encryption"/"encrypted", "adoption"/"adopted").
    ("ptions", "pt"),
    ("ption", "pt"),
    ("ization", "iz"),
    ("isation", "iz"),
    ("ations", "at"),
    ("ation", "at"),
    ("ating", "at"),
    ("ated", "at"),
    ("ates", "at"),
    ("ate", "at"),
    ("izing", "iz"),
    ("ising", "iz"),
    ("ized", "iz"),
    ("ised", "iz"),
    ("izes", "iz"),
    ("ises", "iz"),
    ("iveness", ""),
    ("fulness", ""),
    ("ousness", ""),
    ("ements", ""),
    ("ement", ""),
    ("ments", ""),
    ("ment", ""),
    ("ities", ""),
    ("ity", ""),
    ("ness", ""),
    ("ances", ""),
    ("ance", ""),
    ("ences", ""),
    ("ence", ""),
    ("ables", ""),
    ("able", ""),
    ("ibles", ""),
    ("ible", ""),
    ("sses", "ss"),
    ("ies", "y"),
    ("ing", ""),
    ("edly", ""),
    ("ed", ""),
    ("ly", ""),
    ("es", ""),
    ("s", ""),
)

_MIN_STEM = 3
_KEEP_DOUBLE = {"ll", "ss", "zz", "ff"}


def stem(word: str) -> str:
    """Collapse a word to a normalised stem.

    A compact suffix stripper in the spirit of Porter. It exists to fix the single
    most common keyword-retrieval failure on a small corpus: the user types
    "rate limited" and the document says "rate limits", so the exact-match legs
    score zero on the most discriminative term in the query.

    Linguistic correctness is not the goal — *consistency* is. "rotate",
    "rotated", and "rotation" all collapse to ``rotat``, which is all a matching
    function needs. Deliberately conservative: short words are left alone, and a
    rule never produces a stem below three characters.
    """
    if len(word) <= _MIN_STEM or not word.isalpha():
        return word

    stemmed = word
    # Two passes, because suffixes stack ("businesses" -> "business" -> "busi").
    # Within a pass, a rule whose result would be too short is skipped rather than
    # ending the search, so "fees" falls through "-es" to "-s" and yields "fee".
    for _ in range(2):
        for suffix, replacement in _SUFFIX_RULES:
            if not stemmed.endswith(suffix):
                continue
            candidate = stemmed[: -len(suffix)] + replacement
            if len(candidate) >= _MIN_STEM:
                stemmed = candidate
                break
        else:
            break  # no rule applied — already at a fixpoint
        if len(stemmed) <= _MIN_STEM:
            break

    # Undouble a final consonant pair left behind by "-ed"/"-ing" stripping
    # ("submitted" -> "submitt" -> "submit").
    if (
        len(stemmed) > _MIN_STEM
        and stemmed[-1] == stemmed[-2]
        and stemmed[-2:] not in _KEEP_DOUBLE
        and stemmed[-1].isalpha()
    ):
        stemmed = stemmed[:-1]

    # Drop a silent trailing "e" so "capture"/"captured" agree ("captur").
    if len(stemmed) > _MIN_STEM + 1 and stemmed.endswith("e"):
        stemmed = stemmed[:-1]

    return stemmed


def lexical_tokens(text: str) -> list[str]:
    """Stopword-filtered, stemmed tokens — the key space for all lexical matching.

    Used by BM25, the lexical reranker, the grounding check, and token-F1 so that
    every lexical comparison in the system shares one vocabulary. Mixing stemmed
    and unstemmed keys across stages would silently break IDF weighting.
    """
    return [stem(token) for token in content_tokens(text)]


def topical_tokens(text: str) -> list[str]:
    """Stemmed tokens with question scaffolding removed.

    Only for the question-coverage gate — see :data:`QUESTION_SCAFFOLDING`.
    """
    scaffolding = {stem(word) for word in QUESTION_SCAFFOLDING}
    return [token for token in lexical_tokens(text) if token not in scaffolding]


def count_tokens(text: str) -> int:
    """Approximate the model token count of ``text``.

    Calibrated to run slightly high so that chunk budgets are respected. See the
    module docstring for why this is an approximation and when to use the exact
    ``count_tokens`` API instead.
    """
    if not text.strip():
        return 0
    total = 0
    for atom in _ATOM_RE.findall(text):
        if not atom.isalnum():
            total += 1  # punctuation is almost always its own token
        elif len(atom) <= 4:
            total += 1
        else:
            # Longer words split into subwords; ~4 chars each, rounded up.
            total += math.ceil(len(atom) / 4)
    return total


# Structural lines come in two kinds, and the difference matters.
#
# Self-contained: a heading or a table row is complete on its own line and never
# has continuation lines. Merging a heading with the sentence after it produces
# spans like "### Evidence window **You have 7 calendar days...**", which then
# score badly on density and lose extraction to a weaker sentence.
_SELF_CONTAINED_LINE_RE = re.compile(r"^\s*(?:#{1,6}\s|\|)")
# Continuable: a list item or blockquote may be hard-wrapped across several
# physical lines, and those continuations belong to it.
_CONTINUABLE_LINE_RE = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s|>)")


def logical_lines(text: str) -> list[str]:
    """Merge soft-wrapped prose lines, keeping structural lines separate.

    Markdown source is usually hard-wrapped at 80-100 columns, so a single
    sentence spans several physical lines. Splitting on ``\n`` would emit
    "This is" as its own sentence, so consecutive prose lines are joined.

    Structure is preserved by treating headings and table rows as self-contained
    lines, and list items and blockquotes as lines that may absorb wrapped
    continuations. See the regexes above for why that distinction is load-bearing.
    """
    out: list[str] = []
    buffer: list[str] = []

    def flush() -> None:
        if buffer:
            out.append(" ".join(buffer).strip())
            buffer.clear()

    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            flush()
            continue
        if _SELF_CONTAINED_LINE_RE.match(raw):
            flush()
            out.append(line)
            continue
        if _CONTINUABLE_LINE_RE.match(raw):
            # Starts a new logical line, but its own wrapped continuations still
            # belong to it: a list item split over two physical lines is one item.
            flush()
        buffer.append(line)
    flush()
    return [line for line in out if line]


def split_sentences(text: str) -> list[str]:
    """Split text into sentences, tolerating common abbreviations.

    Soft-wrapped prose is rejoined first (see :func:`logical_lines`), so a
    sentence broken across physical lines is not emitted as two fragments.
    """
    sentences: list[str] = []
    for block in logical_lines(text):
        if not block:
            continue
        parts = _SENTENCE_END_RE.split(block)
        buffer = ""
        for part in parts:
            candidate = f"{buffer} {part}".strip() if buffer else part
            last_word = candidate.split()[-1].lower() if candidate.split() else ""
            if last_word in _ABBREVIATIONS:
                buffer = candidate  # false boundary — keep accumulating
                continue
            sentences.append(candidate)
            buffer = ""
        if buffer:
            sentences.append(buffer)
    return sentences


def overlap_ratio(candidate: str, reference: str) -> float:
    """Fraction of ``candidate``'s content tokens that also appear in ``reference``.

    This is the grounding proxy used by the citation enforcer and by the
    evaluation harness's faithfulness metric. It is a *recall of the answer into
    the sources* measure: 1.0 means every content word in the answer is present
    somewhere in the cited passages.
    """
    cand = lexical_tokens(candidate)
    if not cand:
        return 0.0
    ref = set(lexical_tokens(reference))
    if not ref:
        return 0.0
    hits = sum(1 for token in cand if token in ref)
    return hits / len(cand)


def token_f1(prediction: str, reference: str) -> float:
    """Token-level F1 between two strings — the standard SQuAD-style metric."""
    pred = lexical_tokens(prediction)
    ref = lexical_tokens(reference)
    if not pred or not ref:
        return 1.0 if not pred and not ref else 0.0

    ref_counts: dict[str, int] = {}
    for token in ref:
        ref_counts[token] = ref_counts.get(token, 0) + 1

    common = 0
    for token in pred:
        if ref_counts.get(token, 0) > 0:
            ref_counts[token] -= 1
            common += 1

    if common == 0:
        return 0.0
    precision = common / len(pred)
    recall = common / len(ref)
    return 2 * precision * recall / (precision + recall)


def best_sentence(query: str, text: str) -> str:
    """Return the sentence in ``text`` with the highest content overlap with ``query``.

    Used to attach a short supporting quote to each citation, so a reader can see
    *why* a source was cited without opening the whole chunk.
    """
    sentences = split_sentences(text)
    # Headings and table rows are indexed on purpose -- they carry retrieval
    # signal -- but a citation quote exists to show the reader *why* a source was
    # cited, and "### Dispute fees" shows nothing. Prefer real prose, falling back
    # to the raw list only if a chunk is nothing but structure.
    prose = [s for s in sentences if not s.lstrip().startswith(("#", "|", "```", "~~~"))]
    sentences = prose or sentences
    if not sentences:
        return text[:280]
    query_tokens = set(lexical_tokens(query))
    if not query_tokens:
        return sentences[0]

    def score(sentence: str) -> float:
        tokens = lexical_tokens(sentence)
        if not tokens:
            return 0.0
        # Length-normalised overlap: reward density, not just long sentences.
        return sum(1 for t in tokens if t in query_tokens) / math.sqrt(len(tokens))

    return max(sentences, key=score)


def truncate(text: str, max_chars: int) -> str:
    """Truncate on a word boundary with an ellipsis."""
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars].rsplit(" ", 1)[0]
    return f"{cut}…"
