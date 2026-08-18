"""Tests for the shared text layer.

Every lexical comparison in the system routes through these functions, so a
regression here silently degrades retrieval, reranking, grounding, and evaluation
at the same time.
"""

from __future__ import annotations

import pytest

from askmydocs.text import (
    QUESTION_SCAFFOLDING,
    best_sentence,
    content_tokens,
    count_tokens,
    lexical_tokens,
    logical_lines,
    overlap_ratio,
    split_sentences,
    stem,
    token_f1,
    topical_tokens,
    truncate,
)


class TestStemmer:
    @pytest.mark.parametrize(
        ("left", "right"),
        [
            ("limited", "limits"),
            ("limited", "limit"),
            ("refunded", "refunds"),
            ("rotated", "rotation"),
            ("captured", "capture"),
            ("disputes", "dispute"),
            ("fees", "fee"),
            ("signature", "signatures"),
            ("verifying", "verify"),
            ("payments", "payment"),
            ("rates", "rate"),
            ("encryption", "encrypted"),
            ("settlement", "settlements"),
            ("retries", "retry"),
            ("authorisation", "authorised"),
        ],
    )
    def test_inflections_collapse_to_one_stem(self, left: str, right: str) -> None:
        # The stemmer's contract is consistency, not linguistic correctness:
        # both surface forms must map to the same key or lexical matching fails.
        assert stem(left) == stem(right)

    def test_short_words_are_left_alone(self) -> None:
        for word in ("fee", "was", "key", "api", "is", "a"):
            assert stem(word) == word

    def test_non_alphabetic_tokens_pass_through(self) -> None:
        # Error codes and numbers must survive intact — they are the highest-value
        # retrieval terms in an API corpus.
        for token in ("429", "2024", "v1", "sk_live_9f2c"):
            assert stem(token) == token

    def test_never_produces_a_stem_below_three_characters(self) -> None:
        for word in ("does", "goes", "ones", "ends", "ages"):
            assert len(stem(word)) >= 3

    def test_is_idempotent(self) -> None:
        for word in ("encryption", "settlements", "businesses", "authorisation"):
            assert stem(stem(word)) == stem(word)


class TestSentenceSplitting:
    def test_soft_wrapped_prose_is_rejoined(self) -> None:
        text = "The window is seven days.\nThis is shorter than the network's\nown deadline."
        assert split_sentences(text) == [
            "The window is seven days.",
            "This is shorter than the network's own deadline.",
        ]

    def test_terminator_inside_bold_still_splits(self) -> None:
        # Markdown puts the period inside the emphasis span. A naive lookbehind
        # sees '*' before the space and merges two sentences into one.
        text = "**You have 7 calendar days to submit evidence.** This is shorter than the deadline."
        assert split_sentences(text) == [
            "**You have 7 calendar days to submit evidence.**",
            "This is shorter than the deadline.",
        ]

    def test_headings_are_self_contained(self) -> None:
        text = "### Evidence window\n**You have 7 days.** Then it closes."
        assert split_sentences(text)[0] == "### Evidence window"

    def test_list_item_absorbs_its_wrapped_continuation(self) -> None:
        text = "- **Key management:** data keys are wrapped by an HSM and rotated\n  every 90 days."
        assert logical_lines(text) == [
            "- **Key management:** data keys are wrapped by an HSM and rotated every 90 days."
        ]

    def test_table_rows_stay_separate(self) -> None:
        assert logical_lines("| Plan | Limit |\n| Free | 10/s |") == [
            "| Plan | Limit |",
            "| Free | 10/s |",
        ]

    def test_abbreviations_do_not_end_a_sentence(self) -> None:
        assert split_sentences("Use SEPA, e.g. for EUR payouts. Then reconcile.") == [
            "Use SEPA, e.g. for EUR payouts.",
            "Then reconcile.",
        ]

    def test_empty_input(self) -> None:
        assert split_sentences("") == []
        assert split_sentences("   \n\n  ") == []


class TestTokenCounting:
    def test_empty_text_is_zero(self) -> None:
        assert count_tokens("") == 0
        assert count_tokens("   ") == 0

    def test_grows_monotonically_with_length(self) -> None:
        short = count_tokens("The refund window is fourteen days.")
        long = count_tokens("The refund window is fourteen days. " * 10)
        assert long > short

    def test_estimate_is_in_a_sane_range_for_english(self) -> None:
        # ~0.25-0.6 tokens per character is the plausible band for English prose;
        # outside it the chunker's budgets would be meaningfully wrong.
        text = "Aurora settles in EUR, GBP, USD and CAD. Payouts are daily by default."
        ratio = count_tokens(text) / len(text)
        assert 0.2 <= ratio <= 0.6

    def test_punctuation_counts_as_tokens(self) -> None:
        assert count_tokens("a, b, c") > count_tokens("a b c") - 1


class TestTokenSets:
    def test_stopwords_removed_from_content_tokens(self) -> None:
        assert "the" not in content_tokens("the refund window")
        assert "refund" in content_tokens("the refund window")

    def test_lexical_tokens_are_stemmed(self) -> None:
        assert lexical_tokens("refunded disputes") == [stem("refunded"), stem("disputes")]

    def test_topical_tokens_drop_question_scaffolding(self) -> None:
        tokens = topical_tokens("How often are keys rotated?")
        assert stem("often") not in tokens
        assert stem("rotated") in tokens

    def test_scaffolding_set_contains_no_domain_nouns(self) -> None:
        # A domain noun here would blind the out-of-scope gate to exactly the
        # questions it exists to reject.
        forbidden = {"refund", "dispute", "payout", "webhook", "fee", "card", "payment"}
        assert not (QUESTION_SCAFFOLDING & forbidden)


class TestSimilarity:
    def test_overlap_ratio_is_one_for_a_verbatim_quote(self) -> None:
        source = "The dispute fee is 15.00 EUR and is refunded if you win."
        assert overlap_ratio("The dispute fee is 15.00 EUR.", source) == 1.0

    def test_overlap_ratio_is_zero_for_unrelated_text(self) -> None:
        assert overlap_ratio("penguins migrate annually", "the dispute fee is fifteen euros") == 0.0

    def test_overlap_ratio_handles_empty_inputs(self) -> None:
        assert overlap_ratio("", "anything") == 0.0
        assert overlap_ratio("anything", "") == 0.0

    def test_overlap_ratio_is_asymmetric(self) -> None:
        # It measures recall of the candidate into the reference, which is what
        # the grounding gate needs: every claim must appear in the sources.
        short, long = "dispute fee", "dispute fee is fifteen euros and refunded on a win"
        assert overlap_ratio(short, long) == 1.0
        assert overlap_ratio(long, short) < 1.0

    def test_token_f1_identical_and_disjoint(self) -> None:
        assert token_f1("dispute fee is fifteen", "dispute fee is fifteen") == 1.0
        assert token_f1("penguins", "dispute") == 0.0

    def test_token_f1_is_symmetric(self) -> None:
        a, b = "the dispute fee is fifteen euros", "dispute fee fifteen"
        assert token_f1(a, b) == pytest.approx(token_f1(b, a))

    def test_best_sentence_picks_the_relevant_one(self) -> None:
        text = "Payouts are daily. The dispute fee is 15.00 EUR. Widgets are blue."
        assert "dispute fee" in best_sentence("How much is the dispute fee?", text)


class TestTruncate:
    def test_short_text_is_untouched(self) -> None:
        assert truncate("short", 100) == "short"

    def test_truncates_on_a_word_boundary(self) -> None:
        result = truncate("one two three four five six", 12)
        assert result.endswith("…")
        assert "thre" not in result.replace("three", "")
