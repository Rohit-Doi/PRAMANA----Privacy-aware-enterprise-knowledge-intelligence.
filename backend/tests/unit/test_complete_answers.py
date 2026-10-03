"""Answers that stop one step short, calculations, and identifiers for entitled readers."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from pramana.agents.completeness import bridge_terms, expected_answer_type, missing_answer_type
from pramana.agents.extractive import extractive_follow_up
from pramana.agents.nodes.response_generation import evidence_block, split_lookup
from pramana.privacy.pii import Pseudonymizer, get_pii_engine
from pramana.retrieval.reader import Reading
from pramana.shared.types import Sensitivity, Verdict
from pramana.validation.engine import _decide
from pramana.validation.facts import check_facts

# ── Does the draft give the kind of answer that was asked for? ─────────────────


@pytest.mark.parametrize(
    ("question", "kind"),
    [
        ("Who, by name, approves overtime above 12 hours?", "person"),
        ("On what date will the Board take its final decision?", "date"),
        ("How much is the night shift allowance per shift?", "amount"),
        ("What is the deadline for the W-LV2 form?", "date"),
        ("Which areas count as Zone 3?", ""),
    ],
)
def test_the_kind_of_answer_a_question_asks_for(question: str, kind: str) -> None:
    assert expected_answer_type(question) == kind


def test_an_office_is_not_a_person_and_a_meeting_is_not_a_date() -> None:
    who = "Which person, by name, has to approve overtime above 12 hours?"
    assert missing_answer_type(who, "Overtime needs the approval of the Director of Operations [E1].") == "person"
    assert missing_answer_type(who, "The Director of Operations, Ms. Renata Holbrook, approves it [E1][E7].") == ""
    assert missing_answer_type(who, "[PERSON_2] approves it [E1].") == ""
    assert missing_answer_type(who, "Renata Holbrook approves it [E1].", has_person=True) == ""

    when = "On what date will the Board take its final decision?"
    assert missing_answer_type(when, "The decision is taken at the last ordinary meeting of 2026 [E1].") == "date"
    assert missing_answer_type(when, "The Board decides on 26 November 2026 [E1][E4].") == ""

    fee = "How much is the fee for a room in the hall?"
    assert missing_answer_type(fee, "The room is charged at Residence Band 3 [E1].") == "amount"
    assert missing_answer_type(fee, "Residence Band 3 costs $6,480 a year [E2].") == ""
    assert missing_answer_type("Which areas count as Zone 3?", "The maintenance berth [E1].") == ""


def test_terms_to_look_up_are_the_offices_and_codes_the_question_did_not_name() -> None:
    question = "Who signed off the overtime worked by the tram operator?"
    draft = "The overtime had been approved in writing by the General Manager, Central Division [E1]."
    assert bridge_terms(draft, question) == ["General Manager, Central Division"]

    draft = "Questions about the allowance are decided by the Marine Superintendent under form E-2047 [E1]."
    assert bridge_terms(draft, "Who decides questions about the uniform allowance?") == [
        "Marine Superintendent",
        "E-2047",
    ]
    # A term the question already names leads nowhere new.
    assert bridge_terms("The Board decides [E1].", "When does the Board decide?") == []


# ── A second quote for the extractive route ────────────────────────────────────


@dataclass
class Passage:
    ref: str
    text: str


class FakeReader:
    def __init__(self, answers: dict[tuple[str, str], tuple[str, float]]) -> None:
        self.answers = answers
        self.asked: list[str] = []

    async def read(self, question: str, passages: list[str]) -> list[Reading | None]:
        self.asked.append(question)
        out: list[Reading | None] = []
        for passage in passages:
            found = self.answers.get((question, passage))
            if found is None:
                out.append(None)
                continue
            start = passage.index(found[0])
            out.append(Reading(start, start + len(found[0]), found[0], found[1]))
        return out


REGISTER = "Register of senior officers. The Marine Superintendent is Ms. Ilse Dornan. The Registrar is Mr. P. Vane."


async def test_the_follow_up_asks_who_holds_the_office_and_quotes_the_sentence() -> None:
    reader = FakeReader({("Who is the Marine Superintendent?", REGISTER): ("Ms. Ilse Dornan", 5.0)})
    extra = await extractive_follow_up(
        "Who decides questions about the allowance?",
        "person",
        ["Marine Superintendent"],
        [Passage("E9", REGISTER)],  # type: ignore[list-item]
        reader,  # type: ignore[arg-type]
    )
    assert extra == "The Marine Superintendent is Ms. Ilse Dornan [E9]."
    assert reader.asked == ["Who is the Marine Superintendent?"]


async def test_no_second_quote_without_a_confident_reading() -> None:
    reader = FakeReader({("Who is the Marine Superintendent?", REGISTER): ("Ms. Ilse Dornan", 0.2)})
    passages = [Passage("E9", REGISTER)]
    assert await extractive_follow_up("Who decides?", "person", ["Marine Superintendent"], passages, reader) == ""  # type: ignore[arg-type]
    assert await extractive_follow_up("Who decides?", "person", ["Marine Superintendent"], [], reader) == ""  # type: ignore[arg-type]


# ── The LOOKUP line and the passage header ─────────────────────────────────────


def test_the_lookup_line_is_taken_out_of_the_draft() -> None:
    draft, query = split_lookup("The Director of Operations approves it [E1].\nLOOKUP: Director of Operations holder")
    assert draft == "The Director of Operations approves it [E1]."
    assert query == "Director of Operations holder"
    assert split_lookup("INSUFFICIENT_EVIDENCE\nLOOKUP: annual leave entitlement") == (
        "INSUFFICIENT_EVIDENCE",
        "annual leave entitlement",
    )
    assert split_lookup("Leave is 24 days [E1].") == ("Leave is 24 days [E1].", "")


@dataclass
class Shown:
    ref: str
    title: str
    text: str
    region: str
    section_path: str = ""
    effective_date: str = ""
    is_current: bool = True


def test_a_regional_passage_says_which_region_it_is_for() -> None:
    block = evidence_block(
        [
            Shown("E1", "West Division leave rules", "Leave is 24 working days.", "amer"),  # type: ignore[list-item]
            Shown("E2", "Staff handbook", "Leave must be booked in advance.", "global"),  # type: ignore[list-item]
        ]
    )
    first, second = block.split("\n\n")
    assert "applies to the AMER region only" in first
    assert "region" not in second


# ── Calculations ───────────────────────────────────────────────────────────────

PAY = (
    "The OP2 scale minimum is $46,800 a year. The hourly rate is the annual salary divided by 2,080. "
    "The first 6 hours of overtime in a week are paid at one and a half times the hourly rate. "
    "The capital ceiling is $34.5 million and the recommended contract value is $32,832,000."
)


def test_a_written_out_calculation_is_checked_and_its_result_allowed() -> None:
    derived: set[str] = set()
    first = check_facts("The hourly rate is $46,800 / 2,080 = $22.50 [E1].", PAY, derived=derived)
    assert first.calculations and first.numbers_ok
    # The result of one sentence may be used in the next.
    second = check_facts("An overtime hour pays 1.5 × $22.50 = $33.75 [E1].", PAY, derived=derived)
    assert second.calculations and second.numbers_ok
    scaled = check_facts("The award is $34.5 million - $32,832,000 = $1,668,000 below the ceiling.", PAY)
    assert scaled.calculations and scaled.numbers_ok


def test_wrong_arithmetic_or_an_invented_operand_is_not_accepted() -> None:
    wrong = check_facts("The hourly rate is $46,800 / 2,080 = $25.00.", PAY)
    assert not wrong.calculations and wrong.numbers_missing == ["25"]
    invented = check_facts("The hourly rate is $52,000 / 2,080 = $25.00.", PAY)
    assert not invented.calculations and "52000" in invented.numbers_missing
    # A figure that is simply stated, with no calculation, still has to be in the evidence.
    assert check_facts("An overtime hour pays $33.75.", PAY).numbers_missing == ["33.75"]


def test_a_checked_calculation_counts_as_support() -> None:
    signals = {
        "grounding": 0.3,
        "entailment": 0.2,
        "contradiction": 0.0,
        "contradiction_sentence": 0.0,
        "numbers_ok": 1.0,
        "names_ok": 1.0,
        "numbers_missing": [],
        "names_missing": [],
        "invalid_citations": [],
        "conflicting_evidence": [],
        "conflict_with_current": False,
        "evidence_changed": False,
        "calculations": ["$46,800 / 2,080 = $22.50"],
    }
    assert _decide(signals, 0.2, 0.74)[0] == Verdict.SUPPORTED
    assert _decide({**signals, "calculations": []}, 0.2, 0.74)[0] != Verdict.SUPPORTED
    # The rules that overrule everything still come first.
    missing = {**signals, "numbers_ok": 0.0, "numbers_missing": ["25"]}
    assert _decide(missing, 0.2, 0.74)[0] == Verdict.UNSUPPORTED


# ── Identifiers for readers who may see them ───────────────────────────────────

RECORD = "Jovan Mikkelsen, deckhand. Social security number 536-22-8841. Joined in 2019."


def test_an_identifier_never_leaves_but_is_put_back_for_an_entitled_reader() -> None:
    pseudonymizer = Pseudonymizer(get_pii_engine())
    sent = pseudonymizer.protect(RECORD, sensitivity_rank=Sensitivity.RESTRICTED, restorable_ids=True)
    assert "536-22-8841" not in sent and "[US_SSN_1]" in sent
    assert pseudonymizer.high_risk_removed["US_SSN"] == 1
    answer = "The number recorded is [US_SSN_1] [E1]."
    assert pseudonymizer.restore(answer, reveal=True) == "The number recorded is 536-22-8841 [E1]."
    assert pseudonymizer.restore(answer, reveal=False) == "The number recorded is [REDACTED:US_SSN] [E1]."


def test_without_the_entitlement_an_identifier_is_removed_for_good() -> None:
    pseudonymizer = Pseudonymizer(get_pii_engine())
    sent = pseudonymizer.protect(RECORD, sensitivity_rank=Sensitivity.RESTRICTED)
    assert "[REDACTED:US_SSN]" in sent and "[US_SSN_1]" not in sent
    assert "536-22-8841" not in pseudonymizer.restore(sent, reveal=True)


# ── Later additions: offices, records, units, two passages ─────────────────────


def test_an_office_title_is_not_taken_for_a_persons_name() -> None:
    from pramana.agents.completeness import names_an_office

    assert names_an_office("Marine Superintendent") and names_an_office("Director of Operations")
    assert not names_an_office("Ilse Dornan")


def test_a_lookup_query_contributes_offices_and_codes_only() -> None:
    lookup = "Who is the current Director of Operations for the Brennholt Regional Transit Authority?"
    assert bridge_terms(lookup, "Who approves overtime?", titles=False) == ["Director of Operations"]


def test_protected_values_in_one_row_are_a_record() -> None:
    from pramana.validation.engine import same_record

    register = (
        "Staff register\nName: [PERSON_1]; Staff ID: [US_SSN_2]; Grade: M2\nName: [PERSON_3]; Staff ID: [US_SSN_4]"
    )
    assert same_record("The staff ID recorded for [PERSON_1] is [US_SSN_2] [E1].", register)
    # Values from two different rows are not one record, and one value alone links nothing.
    assert not same_record("The staff ID recorded for [PERSON_1] is [US_SSN_4] [E1].", register)
    assert not same_record("[PERSON_1] is on grade M2 [E1].", register)


def test_a_figure_given_in_the_units_of_a_table_header_can_be_an_operand() -> None:
    table = "Capital programme ($ million). Battery-electric buses: 34.5. The contract value is $32,832,000."
    check = check_facts("The margin is $34,500,000 - $32,832,000 = $1,668,000.", table)
    assert check.calculations and check.numbers_ok
    # The unit spelling of the result is not thereby a free-standing figure.
    assert check_facts("The margin is $1.668.", table).numbers_missing == ["1.668"]


def test_only_the_lines_naming_a_term_are_judged_for_it() -> None:
    from pramana.retrieval.retriever import _lines_with

    register = "Post: Chief Executive; Holder: A\nPost: Marine Superintendent; Holder: Captain Ilse Dornan\nPost: Registrar; Holder: B"
    assert _lines_with(register, "marine superintendent") == "Post: Marine Superintendent; Holder: Captain Ilse Dornan"
    assert _lines_with(register, "Harbour Master") == register
