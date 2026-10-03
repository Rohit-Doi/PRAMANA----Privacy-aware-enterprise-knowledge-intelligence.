"""Quoting an answer from evidence: the reader decides what answers, and whether anything does."""

from __future__ import annotations

from dataclasses import dataclass

from pramana.agents.extractive import extractive_answer, quotable
from pramana.retrieval.reader import Reading

MEMO = (
    "Aurelia Systems proposes to acquire Brightwater Analytics. "
    "The proposed purchase price is $412.5 million, paid 70% in cash and 30% in stock. "
    "Signing is targeted for 14 November 2026."
)
BANDS = "Base salary bands\n\n| Level | Minimum | Maximum |\n| L3 | $112,000 | $161,000 |\nBands are reviewed yearly."


@dataclass
class Passage:
    ref: str
    text: str
    rerank_score: float


class FakeReader:
    """Returns a prepared span and margin for each passage text."""

    def __init__(self, answers: dict[str, tuple[str, float]]) -> None:
        self.answers = answers
        self.seen: list[str] = []

    async def read(self, question: str, passages: list[str]) -> list[Reading | None]:
        self.seen = passages
        out: list[Reading | None] = []
        for passage in passages:
            if passage not in self.answers:
                out.append(None)
                continue
            span, margin = self.answers[passage]
            start = passage.index(span)
            out.append(Reading(start, start + len(span), span, margin))
        return out


async def test_the_sentence_around_the_answer_is_quoted_with_its_reference() -> None:
    reader = FakeReader({MEMO: ("$412.5 million", 6.5)})
    answer = await extractive_answer("What is the purchase price?", [Passage("E1", MEMO, 9.0)], reader)  # type: ignore[arg-type, list-item]
    assert answer == "The proposed purchase price is $412.5 million, paid 70% in cash and 30% in stock [E1]."


async def test_nothing_is_quoted_when_the_reader_finds_no_answer() -> None:
    reader = FakeReader({MEMO: ("$412.5 million", -9.6)})
    passages = [Passage("E1", MEMO, 0.5)]
    assert await extractive_answer("What is the price of the Corvane acquisition?", passages, reader) == ""  # type: ignore[arg-type]
    assert await extractive_answer("Anything?", [], reader) == ""  # type: ignore[arg-type]


async def test_passages_are_read_in_order_of_relevance_and_only_near_the_best() -> None:
    other = "The 2024 policy allowed 2 remote days per week. It has been superseded."
    current = "Full-time employees may work remotely for up to 3 days per week."
    distant = "Contractors badge in at reception. Visitors must be escorted at all times."
    reader = FakeReader({current: ("3 days", 5.9), other: ("2 remote days", 7.1), distant: ("reception", 8.0)})
    passages = [Passage("E1", current, 9.5), Passage("E2", other, 8.1), Passage("E3", distant, 3.0)]
    answer = await extractive_answer("How many remote days?", passages, reader)  # type: ignore[arg-type]
    # The most relevant passage that answers wins, even if a later one scores higher with the reader.
    assert answer.endswith("[E1].") and "3 days" in answer
    # A passage far below the best in relevance is not read at all.
    assert distant not in reader.seen


async def test_the_next_passage_is_used_when_the_first_does_not_answer() -> None:
    first = "Every document carries a label. Labels are set by the owner."
    second = "The four labels are PUBLIC, INTERNAL, CONFIDENTIAL and RESTRICTED."
    reader = FakeReader({first: ("a label", -1.1), second: ("PUBLIC, INTERNAL, CONFIDENTIAL and RESTRICTED", 2.3)})
    passages = [Passage("E1", first, 6.4), Passage("E2", second, 6.0)]
    answer = await extractive_answer("What are the four labels?", passages, reader)  # type: ignore[arg-type]
    assert answer.endswith("[E2].")


def test_table_rows_and_headings_are_not_quoted() -> None:
    start = BANDS.index("$161,000")
    assert quotable(BANDS, start, start + len("$161,000")) == ""
    start = BANDS.index("Base salary")
    assert quotable(BANDS, start, start + 4) == ""
    start = MEMO.index("14 November")
    assert quotable(MEMO, start, start + 11) == "Signing is targeted for 14 November 2026."


def test_a_span_across_two_sentences_quotes_both() -> None:
    start = MEMO.index("Brightwater")
    end = MEMO.index("$412.5 million") + 5
    quote = quotable(MEMO, start, end)
    assert quote.startswith("Aurelia Systems proposes") and quote.endswith("30% in stock.")


async def test_a_less_relevant_passage_is_quoted_only_when_the_reader_is_clearly_confident() -> None:
    top = "The overpayment was discovered during the annual payroll audit. It covered eleven months."
    lower = "The President has authorised a settlement of up to $75,000, inclusive of costs."
    passages = [Passage("E1", top, 2.7), Passage("E2", lower, -5.0)]
    unsure = FakeReader({top: ("eleven months", -0.9), lower: ("$75,000", 1.2)})
    assert await extractive_answer("What settlement was authorised?", passages, unsure) == ""  # type: ignore[arg-type]
    confident = FakeReader({top: ("eleven months", -0.9), lower: ("$75,000", 4.0)})
    answer = await extractive_answer("What settlement was authorised?", passages, confident)  # type: ignore[arg-type]
    assert answer.endswith("[E2].") and "$75,000" in answer


def test_a_lookup_line_is_split_from_the_draft() -> None:
    from pramana.agents.nodes.response_generation import split_lookup

    draft, query = split_lookup(
        "Purchases above $40,000 need the approval of the Dean of Research [E1].\nLOOKUP: Dean of Research name"
    )
    assert draft == "Purchases above $40,000 need the approval of the Dean of Research [E1]."
    assert query == "Dean of Research name"
    assert split_lookup("INSUFFICIENT_EVIDENCE\nlookup: <Registrar of the institute>") == (
        "INSUFFICIENT_EVIDENCE",
        "Registrar of the institute",
    )
    assert split_lookup("A plain answer [E1].") == ("A plain answer [E1].", "")
