"""The deterministic parts of the validation engine: claims, literal checks, alignment, calibration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pramana.validation.alignment import best_window, content_tokens
from pramana.validation.calibration import FEATURES, Calibrator
from pramana.validation.claims import citations_in, extract_claims, is_insufficient, strip_citations
from pramana.validation.facts import check_facts, numbers_in


# ── Claims ──────────────────────────────────────────────────────────────────
def test_each_sentence_becomes_a_claim_with_its_citations() -> None:
    answer = "Leave is 25 days per year [E1]. Up to 5 days carry over [E1, E3]. Requests go to the manager [E2]"
    claims = extract_claims(answer)
    assert [c.text for c in claims] == [
        "Leave is 25 days per year.",
        "Up to 5 days carry over.",
        "Requests go to the manager",
    ]
    assert [c.cited for c in claims] == [["E1"], ["E1", "E3"], ["E2"]]
    for claim in claims:
        assert answer[claim.start : claim.end] == claim.raw


def test_a_citation_after_the_full_stop_belongs_to_the_previous_sentence() -> None:
    claims = extract_claims("Laptops are encrypted. [E2] Phones are enrolled in MDM [E4].")
    assert len(claims) == 2
    assert claims[0].cited == ["E2"]
    assert claims[1].cited == ["E4"]


def test_bullets_are_stripped_and_empty_fragments_ignored() -> None:
    claims = extract_claims("- Receipts are required [E1].\n- Claims are due in 30 days [E1].\n\n[E1]")
    assert [c.text for c in claims] == ["Receipts are required.", "Claims are due in 30 days."]


def test_citation_helpers() -> None:
    assert strip_citations("Approved by Finance [E1; E2] .") == "Approved by Finance."
    assert citations_in("a [E1] b [E2, E1] c [E10]") == ["E1", "E2", "E10"]
    assert is_insufficient("INSUFFICIENT_EVIDENCE")
    assert not is_insufficient("The evidence shows a limit of 500 [E1].")


# ── Literal checks ──────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("claim", "evidence", "ok"),
    [
        ("The limit is 500 dollars.", "Claims are capped at $500 per trip.", True),
        ("The limit is 750 dollars.", "Claims are capped at $500 per trip.", False),
        ("Revenue grew 12.5%.", "Revenue grew by 12.5 % year over year.", True),
        ("Revenue grew 15%.", "Revenue grew by 12.5 % year over year.", False),
        ("The budget is 1,200,000.", "A budget of 1200000 was approved.", True),
        ("Staff get thirty days.", "Staff get 30 days of leave.", True),
        ("There is no figure here.", "Nothing numeric either.", True),
    ],
)
def test_numbers_must_appear_in_the_evidence(claim: str, evidence: str, ok: bool) -> None:
    assert check_facts(claim, evidence).numbers_ok is ok


def test_numbers_are_canonicalised() -> None:
    assert numbers_in("1,200 and 1200 and 3.50") == ["1200", "1200", "3.5"]


def test_names_must_appear_in_the_evidence() -> None:
    evidence = "The policy is owned by Priya Raman, Chief Financial Officer."
    assert check_facts("Priya Raman owns the policy.", evidence).names_ok
    wrong = check_facts("Daniel Okafor owns the policy.", evidence)
    assert not wrong.names_ok
    assert wrong.names_missing == ["Daniel Okafor"]


def test_identifiers_are_checked_literally() -> None:
    evidence = "See ticket SEC-2041 for the remediation plan."
    assert check_facts("The plan is tracked in SEC-2041.", evidence).names_ok
    assert not check_facts("The plan is tracked in SEC-2099.", evidence).names_ok


# ── Alignment ───────────────────────────────────────────────────────────────
EVIDENCE = (
    "The office opens at eight. Visitors must sign in at reception. "
    "Travel claims above 500 dollars need approval from the department head. "
    "Receipts are kept for seven years. The canteen closes at three."
)


def test_best_window_finds_the_supporting_sentence() -> None:
    window = best_window("Claims over 500 dollars require the department head's approval.", EVIDENCE, radius=1)
    assert window.quote.startswith("Travel claims above 500 dollars")
    assert EVIDENCE[window.quote_start : window.quote_end] == window.quote
    assert window.quote in window.text
    assert window.overlap > 0.5


def test_best_window_handles_unrelated_and_empty_text() -> None:
    assert best_window("Quantum computers use qubits.", EVIDENCE).overlap < 0.2
    assert best_window("Anything.", "").text == ""


def test_content_tokens_drop_stop_words() -> None:
    tokens = content_tokens("The approval of the department head is required")
    assert "the" not in tokens and "of" not in tokens
    assert "approval" in tokens or "approv" in "".join(tokens)


# ── Calibration ─────────────────────────────────────────────────────────────
def _signals(**overrides: float) -> dict[str, float]:
    base = dict.fromkeys(FEATURES, 0.0) | {"numbers_ok": 1.0, "names_ok": 1.0}
    return base | overrides


def test_default_calibrator_orders_obvious_cases() -> None:
    calibrator = Calibrator.load(Path("does-not-exist.json"))
    supported = calibrator.probability(_signals(grounding=0.97, entailment=0.95, overlap=0.9))
    unsupported = calibrator.probability(
        _signals(grounding=0.05, entailment=0.02, contradiction=0.9, span_fraction=0.8)
    )
    assert 0.0 <= unsupported < 0.2 < 0.8 < supported <= 1.0


def test_calibrator_round_trips_through_json(tmp_path: Path) -> None:
    original = Calibrator(dict.fromkeys(FEATURES, 0.5), -1.0, "test", {"n": 3}, 0.42)
    path = tmp_path / "calibrator.json"
    path.write_text(original.to_json(), encoding="utf-8")
    loaded = Calibrator.load(path)
    assert loaded == original
    assert loaded.probability(_signals(grounding=1.0)) == original.probability(_signals(grounding=1.0))


def test_calibrator_rejects_a_different_feature_set(tmp_path: Path) -> None:
    path = tmp_path / "calibrator.json"
    path.write_text(json.dumps({"features": ["grounding"], "coefficients": {"grounding": 1.0}, "intercept": 0.0}))
    with pytest.raises(ValueError, match="feature set"):
        Calibrator.load(path)


def test_probability_is_bounded_for_extreme_inputs() -> None:
    calibrator = Calibrator(dict.fromkeys(FEATURES, 100.0), 0.0)
    assert calibrator.probability(dict.fromkeys(FEATURES, 1e6)) <= 1.0
    assert calibrator.probability(dict.fromkeys(FEATURES, -1e6)) >= 0.0
