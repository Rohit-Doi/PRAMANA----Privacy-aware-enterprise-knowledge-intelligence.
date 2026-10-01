"""Personal-data detection, masking for readers and reversible pseudonymisation for the LLM."""

from __future__ import annotations

import pytest

from pramana.privacy.pii import Pseudonymizer, get_pii_engine, mask_for_reader
from pramana.shared.types import Sensitivity

RECORD = (
    "Employee Maria Fernandez (maria.fernandez@aurelia-systems.example, +1 415 555 0134) "
    "has social security number 536-22-8841 and was promoted in March."
)


@pytest.fixture(scope="module")
def engine():  # type: ignore[no-untyped-def]
    return get_pii_engine()


def test_high_risk_identifiers_and_contact_details_are_found(engine) -> None:  # type: ignore[no-untyped-def]
    kinds = {f.entity_type for f in engine.analyze(RECORD)}
    assert {"US_SSN", "EMAIL_ADDRESS", "PHONE_NUMBER", "PERSON"} <= kinds
    for finding in engine.analyze(RECORD):
        assert RECORD[finding.start : finding.end] == finding.text


def test_high_risk_identifiers_are_masked_for_every_reader_without_pii_permission() -> None:
    for rank in (Sensitivity.PUBLIC, Sensitivity.INTERNAL, Sensitivity.CONFIDENTIAL, Sensitivity.RESTRICTED):
        masked = mask_for_reader(RECORD, sensitivity_rank=rank, can_read_pii=False)
        assert "536-22-8841" not in masked
        assert "[REDACTED:US_SSN]" in masked


def test_contact_details_are_masked_only_in_confidential_documents() -> None:
    internal = mask_for_reader(RECORD, sensitivity_rank=Sensitivity.INTERNAL, can_read_pii=False)
    confidential = mask_for_reader(RECORD, sensitivity_rank=Sensitivity.CONFIDENTIAL, can_read_pii=False)
    assert "Maria Fernandez" in internal
    assert "Maria Fernandez" not in confidential
    assert "maria.fernandez@" not in confidential and "555 0134" not in confidential


def test_a_reader_with_pii_permission_sees_the_original() -> None:
    assert mask_for_reader(RECORD, sensitivity_rank=Sensitivity.RESTRICTED, can_read_pii=True) == RECORD


def test_pseudonyms_are_stable_and_reversible(engine) -> None:  # type: ignore[no-untyped-def]
    pseudonymizer = Pseudonymizer(engine)
    first = pseudonymizer.protect(RECORD, sensitivity_rank=Sensitivity.CONFIDENTIAL)
    second = pseudonymizer.protect(
        "Maria Fernandez reports to the Finance director.", sensitivity_rank=Sensitivity.CONFIDENTIAL
    )
    assert "Maria Fernandez" not in first and "536-22-8841" not in first
    assert "[PERSON_1]" in first and "[PERSON_1]" in second  # the same person gets the same placeholder
    assert "[REDACTED:US_SSN]" in first
    assert pseudonymizer.high_risk_removed["US_SSN"] == 1

    answer = "[PERSON_1] was promoted in March [E1]."
    assert pseudonymizer.restore(answer, reveal=True) == "Maria Fernandez was promoted in March [E1]."
    assert pseudonymizer.restore(answer, reveal=False) == "[REDACTED:PERSON] was promoted in March [E1]."
    # A high-risk identifier is never restored: no placeholder maps back to it.
    assert "536-22-8841" not in pseudonymizer.restore(first, reveal=True)


def test_names_in_low_sensitivity_text_are_not_pseudonymised(engine) -> None:  # type: ignore[no-untyped-def]
    pseudonymizer = Pseudonymizer(engine)
    text = "The handbook was written by Maria Fernandez."
    assert pseudonymizer.protect(text, sensitivity_rank=Sensitivity.INTERNAL) == text
    assert pseudonymizer.placeholder_count == 0


def test_citation_markers_and_placeholders_are_not_personal_data(engine) -> None:  # type: ignore[no-untyped-def]
    text = "Leave is 25 days [E1]. Approval is needed [E2, E3]. [PERSON_2] signed it. See [G4]."
    assert engine.redact(text, include_contact=True).text == text


def test_policy_vocabulary_is_not_mistaken_for_people(engine) -> None:  # type: ignore[no-untyped-def]
    text = "The Travel Policy and the Security Committee follow the GDPR and the NIST framework."
    assert [f for f in engine.analyze(text) if f.entity_type == "PERSON"] == []
