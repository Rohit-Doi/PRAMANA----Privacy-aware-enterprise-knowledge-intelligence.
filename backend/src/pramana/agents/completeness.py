"""Does a draft actually answer the question that was asked?

A first draft often stops one step short: asked *who* approves, it says "the Director of
Operations"; asked *when*, it says "at the last ordinary meeting of the year". The statement is
true and supported, and it is not the answer. Two small, deterministic checks find that:

* what kind of thing the question asks for (a person, an amount, a date), and whether the draft
  contains one;
* which terms in the draft point to something defined elsewhere (an office, a committee, a code,
  a band), so that a second search can look them up.
"""

from __future__ import annotations

import re

from pramana.validation.claims import strip_citations

PERSON, AMOUNT, DATE = "person", "amount", "date"

_ASKS_PERSON = re.compile(
    r"\b(?:who|whom|whose)\b|\bby name\b|\bname of the\b|\bnamed?\b.{0,20}\bperson\b|\bwhich (?:person|individual|officer)\b",
    re.I,
)
_ASKS_AMOUNT = re.compile(
    r"\bhow (?:much|many)\b|\bwhat(?:'s| is| are| was| were)? (?:the )?(?:[\w-]+ ){0,3}"
    r"(?:amount|price|cost|fee|fare|rate|charge|salary|pay|rent|budget|total|value|limit|cap|ceiling|"
    r"allowance|penalty|fine|percentage|share|number|score|capacity)\b",
    re.I,
)
_ASKS_DATE = re.compile(
    r"\bwhen\b|\b(?:what|which|on what) (?:date|day)\b|\bby what date\b|\bdeadline\b|\blast date\b|\bbetween which dates\b",
    re.I,
)

_MONTH = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
_HAS_DATE = re.compile(
    rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}\b|\b{_MONTH}\s+\d{{1,2}}\b|\b\d{{4}}-\d{{2}}-\d{{2}}\b|\b\d{{1,2}}/\d{{1,2}}/\d{{2,4}}\b",
    re.I,
)
_HAS_NUMBER = re.compile(r"\d")
_HAS_MONEY = re.compile(r"[$€£₹]\s?\d|\d\s?(?:dollars|euros|pounds|rupees|USD|EUR|GBP|INR)\b", re.I)
_ASKS_MONEY = re.compile(
    r"\bhow much\b|\b(?:amount|price|cost|fee|fare|charge|salary|pay|rent|budget|allowance|penalty|fine|stipend)\b",
    re.I,
)
_HONORIFIC_NAME = re.compile(r"\b(?:Mr|Mrs|Ms|Miss|Dr|Prof|Professor|Sir|Dame)\.?\s+[A-Z][\w'’-]+")
_PERSON_PLACEHOLDER = re.compile(r"\[(?:PERSON_\d+|REDACTED:PERSON)\]")

# Words that head the name of an office, a body or a category rather than of a person.
_ROLE_WORDS = (
    "director|manager|superintendent|officer|warden|dean|head|chair|chairman|chairperson|president|"
    "vice-president|secretary|registrar|controller|counsel|supervisor|coordinator|lead|treasurer|"
    "provost|principal|bursar|auditor|inspector|commissioner|executive|governor|trustee|"
    "committee|board|panel|council|commission|office|department|division|directorate|unit|team|desk|"
    "band|grade|scale|zone|tier|level|category|class|schedule|calendar|register|policy|regulations?|"
    "standing order|form|fund|programme|program|scheme|depot|hall|campus|centre|center"
)
_TITLE_PHRASE = re.compile(r"\b(?:[A-Z][\w&'’-]*\.?[ \t]+(?:(?:of|for|and|the|to|in|on|at)[ \t]+)?){1,5}[A-Z][\w&'’-]*")
_CAP = r"[A-Z][\w&'’-]*"
# "Director of Operations", "General Manager, Central Division", "Finance and Audit Committee",
# "Residence Band 3". Only the role word itself is matched without regard to case.
_ROLE_PHRASE = re.compile(
    rf"(?:\b{_CAP}[ \t]+(?:(?:of|for|and|the)[ \t]+)?){{0,4}}\b(?i:{_ROLE_WORDS})\b"
    rf"(?:[ \t]+(?:of|for)[ \t]+(?:the[ \t]+)?{_CAP}(?:[ \t]+(?:and[ \t]+)?{_CAP}){{0,3}})?"
    rf"(?:,[ \t]+{_CAP}(?:[ \t]+{_CAP}){{0,2}})?"
    r"(?:[ \t]+(?:[A-Z]\d*|\d{1,3})\b)?"
)
_CODE = re.compile(r"\b[A-Z]{1,6}(?:-[A-Z0-9]+)*-?\d+[A-Z0-9-]*\b")
_LEADING = {"the", "a", "an", "if", "in", "on", "for", "where", "when", "any", "each", "this", "these", "under", "by"}
MAX_TERMS = 4


_IS_OFFICE = re.compile(rf"\b(?:{_ROLE_WORDS})\b", re.I)


def names_an_office(text: str) -> bool:
    """ "Marine Superintendent" and "Director of Operations" are posts, whatever a name finder says."""
    return _IS_OFFICE.search(text) is not None


def expected_answer_type(question: str) -> str:
    """ "person", "amount", "date" or "" when the question does not clearly ask for one of these."""
    if _ASKS_PERSON.search(question):
        return PERSON
    if _ASKS_DATE.search(question):
        return DATE
    if _ASKS_AMOUNT.search(question):
        return AMOUNT
    return ""


def missing_answer_type(question: str, draft: str, *, has_person: bool = False) -> str:
    """The kind of answer the question asks for, if the draft does not contain one; otherwise ""."""
    kind = expected_answer_type(question)
    text = strip_citations(draft)
    if kind == PERSON:
        named = has_person or _HONORIFIC_NAME.search(text) or _PERSON_PLACEHOLDER.search(text)
        return "" if named else PERSON
    if kind == DATE:
        return "" if _HAS_DATE.search(text) else DATE
    if kind == AMOUNT:
        # A question about money needs a sum of money, not just any figure ("Band 3" is not a fee).
        needed = _HAS_MONEY if _ASKS_MONEY.search(question) else _HAS_NUMBER
        return "" if needed.search(text) else AMOUNT
    return ""


def bridge_terms(draft: str, question: str, *, titles: bool = True) -> list[str]:
    """Offices, bodies, categories and codes named in the draft that the question did not name.

    These are what a second search looks up: "the Director of Operations" leads to the register that
    says who holds the post, "Band C" to the table that says what the band is worth.
    """
    text = strip_citations(draft)
    asked = question.lower()
    found: list[str] = []

    def consider(raw: str) -> None:
        words = raw.strip(" .,;:()").split()
        while words and words[0].lower() in _LEADING:
            words = words[1:]
        term = " ".join(words)
        if len(term) < 3 or not term[:1].isupper() or term.lower() in asked:
            return
        if any(term.lower() in other.lower() or other.lower() in term.lower() for other in found):
            return
        found.append(term)

    for line in text.splitlines():
        for match in _ROLE_PHRASE.finditer(line):
            # An office or body is written with capitals: "the head of the queue" is not one.
            if match.group(0)[:1].isupper():
                consider(match.group(0))
        for match in _CODE.finditer(line):
            consider(match.group(0))
    if titles and len(found) < MAX_TERMS:
        for line in text.splitlines():
            for match in _TITLE_PHRASE.finditer(line):
                consider(match.group(0))
    return found[:MAX_TERMS]
