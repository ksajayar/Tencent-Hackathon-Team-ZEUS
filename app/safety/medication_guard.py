import re

from app.core.logging import get_logger
from app.db.models.medication import Medication

logger = get_logger(__name__)


def enforce(text: str, medications: list[Medication], *, fallback: str) -> str:
    """SAFETY-1: every outbound message that mentions a medication must contain
    only drug names verbatim from the source rows. Reminder and medication-
    query text is always template-rendered from these exact rows - never LLM-
    generated - so this is a defensive assertion against a future template
    bug, not a hallucination detector. Mismatch -> discard, send the fallback.

    Only correct for text that is SUPPOSED to enumerate every one of
    `medications` (the medication-list reply, a reminder for one specific
    row) - checks that every expected name is present, not that nothing
    unexpected is. Do not call this on ordinary conversation: a reply that
    doesn't happen to mention medications at all would wrongly fail it. See
    screen_for_unlisted_medication() below for that case.
    """
    for medication in medications:
        if medication.name and medication.name not in text:
            logger.error("medication_guard_rejected", medication_id=str(medication.id))
            return fallback
    return text


# Real, common medication names an elderly dementia-care patient plausibly
# takes - dementia, diabetes, cardiac/blood-pressure, blood thinners,
# cholesterol, and common pain relief. NOT an exhaustive pharmacopeia and NOT
# a substitute for a real drug database (e.g. RxNorm) in a non-demo
# deployment - curated only so screen_for_unlisted_medication() below has
# something concrete to check free-text replies against. Extend this list
# before relying on it for a drug class not represented here.
_KNOWN_DRUG_NAMES = frozenset(
    {
        # dementia
        "donepezil",
        "aricept",
        "memantine",
        "namenda",
        "rivastigmine",
        "exelon",
        "galantamine",
        "reminyl",
        # diabetes
        "metformin",
        "glucophage",
        "gliclazide",
        "glipizide",
        "insulin",
        "sitagliptin",
        "januvia",
        # cardiac / blood pressure / blood thinners
        "warfarin",
        "coumadin",
        "aspirin",
        "clopidogrel",
        "plavix",
        "amlodipine",
        "losartan",
        "atenolol",
        "bisoprolol",
        "furosemide",
        "lisinopril",
        "ramipril",
        "digoxin",
        # cholesterol
        "atorvastatin",
        "lipitor",
        "simvastatin",
        "rosuvastatin",
        "crestor",
        # pain / common OTC
        "panadol",
        "paracetamol",
        "acetaminophen",
        "ibuprofen",
        "tramadol",
        "codeine",
        "morphine",
        # other common elderly-care
        "omeprazole",
        "levothyroxine",
        "prednisone",
        "amoxicillin",
    }
)

_LATIN_WORD_RE = re.compile(r"[A-Za-z][A-Za-z\-']{2,}")


def screen_for_unlisted_medication(
    text: str, medications: list[Medication], *, fallback: str
) -> str:
    """SAFETY-1, free-text paths only (_general_qa, _caregiver_qa - anywhere
    the reply is model-generated rather than template-rendered from
    `medications`). The opposite check from enforce() above: not that every
    expected name is present, but that no OTHER real medication name has
    been introduced - the dangerous hallucination mode is the model
    substituting one real, contextually-plausible drug for another (e.g.
    naming "Aricept" when the patient's actual row says "Donepezil" - same
    drug class, different name, and just as credible-sounding either way).
    A nonsense invented word is lower-risk here: a patient or caregiver is
    more likely to notice and question it on its own.

    Drug names stay in Latin script even inside an otherwise-Chinese reply
    (persona rule: English proper nouns and medical/brand terms are never
    translated), so scanning for Latin-script tokens works the same way
    regardless of reply language - this is why the check is limited to the
    curated _KNOWN_DRUG_NAMES set rather than "any unrecognised token": in
    an English-language reply, ordinary English words are Latin-script too,
    and there is no drug database here to tell a real invented-sounding word
    apart from an ordinary one.
    """
    allowed = {m.name.lower() for m in medications if m.name}
    for match in _LATIN_WORD_RE.findall(text):
        token = match.lower()
        if token in _KNOWN_DRUG_NAMES and token not in allowed:
            logger.error("medication_guard_unlisted_drug_blocked")
            return fallback
    return text


# CLAUDE.md SAFETY-1 clause 4 ("never generate dosage advice, drug
# interactions, or 'you can skip this one' reasoning") has no enforcement
# anywhere else in the codebase - it is persona wording only. This is a
# bounded phrase blocklist, not a parser: "take your Donepezil, though you
# could skip it today" passes screen_for_unlisted_medication() above
# (Donepezil is real and listed), because that guard only checks WHICH drug
# names appear, never what is claimed about them. This checks the claim,
# but only by matching a fixed set of dose-modification-shaped phrases - a
# fluent violation this list doesn't anticipate ("perhaps hold off on
# tonight's dose") passes uncaught. Catches the common shapes; not a
# guarantee, same honest ceiling as the guard above.
_DOSAGE_ADVICE_PHRASES_EN = [
    "you could take",
    "you could skip",
    "you can skip",
    "you may skip",
    "extra dose",
    "double dose",
    "take an extra",
    "take two instead",
    "not with your",
    "don't take it with",
    "should not be taken with",
    "increase the dose",
    "reduce the dose",
    "lower the dose",
    "stop taking",
    "hold off on",
]
_DOSAGE_ADVICE_PHRASES_ZH = [
    "可以不吃",
    "可以跳过",
    "不用吃",
    "不用服用",
    "不要和",
    "不能同时吃",
    "不能一起吃",
    "多吃一片",
    "加倍",
    "双倍",
    "减少剂量",
    "增加剂量",
    "停药",
    "停止服用",
]
_DOSAGE_ADVICE_RE = re.compile(
    "|".join(re.escape(p) for p in _DOSAGE_ADVICE_PHRASES_EN + _DOSAGE_ADVICE_PHRASES_ZH),
    re.IGNORECASE,
)


def screen_for_dosage_advice(text: str, medications: list[Medication], *, fallback: str) -> str:
    """CLAUDE.md SAFETY-1 clause 4. Requires BOTH conditions - a listed
    medication actually named in the reply, AND dose-modification-shaped
    language present anywhere in it - so an unrelated sentence in the same
    short reply ("if you feel unwell, call your daughter") can't
    false-positive on the advice phrases alone with no drug in sight. Does
    not parse which sentence the drug name is even in; given replies are
    capped at ~600 chars (simplifier.py) and the persona already enforces
    one idea per sentence, a false trigger from two unrelated clauses in the
    same reply is a real but narrow risk, not solved here.
    """
    if not medications:
        return text
    names_a_medication = any(m.name and m.name in text for m in medications)
    if names_a_medication and _DOSAGE_ADVICE_RE.search(text):
        logger.error("medication_guard_dosage_advice_blocked")
        return fallback
    return text
