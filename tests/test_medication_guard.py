"""§11 priority test 5 (docs/11's own #1: "a wrong drug name reaching a
patient is the worst failure mode") - both medication_guard functions.

`enforce()` is the completeness check for template-rendered text that is
supposed to enumerate every row. `screen_for_unlisted_medication()` is the
contamination check for model-generated free text - it must catch the
model substituting one real, plausible drug for the patient's actual one,
while leaving an invented/nonsense name alone (see the module docstring for
why that's the deliberate, bounded scope). `screen_for_dosage_advice()`
guards CLAUDE.md SAFETY-1 clause 4.
"""

from app.db.models.medication import Medication
from app.safety.medication_guard import (
    enforce,
    screen_for_dosage_advice,
    screen_for_unlisted_medication,
)

FALLBACK = "It is time for your medicine."


def _med(name: str) -> Medication:
    return Medication(name=name, dose_text="1 tablet", schedule_rrule="FREQ=DAILY")


# --- enforce() : completeness -----------------------------------------------


def test_enforce_passes_when_every_medication_present():
    meds = [_med("Donepezil"), _med("Metformin")]
    text = "Take your Donepezil and Metformin now."
    assert enforce(text, meds, fallback=FALLBACK) == text


def test_enforce_blocks_when_a_medication_is_missing():
    meds = [_med("Donepezil"), _med("Metformin")]
    text = "Take your Donepezil now."
    assert enforce(text, meds, fallback=FALLBACK) == FALLBACK


def test_enforce_passes_with_no_medications_to_check():
    assert enforce("Good morning!", [], fallback=FALLBACK) == "Good morning!"


# --- screen_for_unlisted_medication() : contamination -----------------------


def test_screen_passes_when_only_listed_drug_named():
    meds = [_med("Donepezil")]
    text = "It's time to take your Donepezil."
    assert screen_for_unlisted_medication(text, meds, fallback=FALLBACK) == text


def test_screen_blocks_similar_but_different_real_drug_name():
    # The dangerous hallucination mode: model substitutes a real, plausible
    # drug (same class) for the patient's actual medication.
    meds = [_med("Donepezil")]
    text = "It's time to take your Aricept."
    assert screen_for_unlisted_medication(text, meds, fallback=FALLBACK) == FALLBACK


def test_screen_passes_when_no_drug_mentioned():
    meds = [_med("Donepezil")]
    text = "How are you feeling today?"
    assert screen_for_unlisted_medication(text, meds, fallback=FALLBACK) == text


def test_screen_allows_invented_nonsense_word_known_bounded_limit():
    # Documented ceiling: a fully invented name is lower risk and passes.
    meds = [_med("Donepezil")]
    text = "It's time to take your Zorbitrex."
    assert screen_for_unlisted_medication(text, meds, fallback=FALLBACK) == text


def test_screen_handles_chinese_reply_with_latin_drug_name():
    meds = [_med("Donepezil")]
    text = "现在该吃Aricept了。"
    assert screen_for_unlisted_medication(text, meds, fallback=FALLBACK) == FALLBACK

    ok_text = "现在该吃Donepezil了。"
    assert screen_for_unlisted_medication(ok_text, meds, fallback=FALLBACK) == ok_text


def test_screen_is_case_insensitive_on_the_allowed_name():
    meds = [_med("donepezil")]
    text = "Take your Donepezil now."
    assert screen_for_unlisted_medication(text, meds, fallback=FALLBACK) == text


# --- screen_for_dosage_advice() : SAFETY-1 clause 4 -------------------------


def test_dosage_advice_blocked_when_drug_named_and_advice_phrase_present():
    meds = [_med("Donepezil")]
    text = "Take your Donepezil, though you could skip it today."
    assert screen_for_dosage_advice(text, meds, fallback=FALLBACK) == FALLBACK


def test_dosage_advice_passes_when_drug_named_with_no_advice_language():
    meds = [_med("Donepezil")]
    text = "It's time for your Donepezil."
    assert screen_for_dosage_advice(text, meds, fallback=FALLBACK) == text


def test_dosage_advice_passes_when_advice_phrase_present_but_no_drug_named():
    meds = [_med("Donepezil")]
    text = "You could skip the walk today if you're tired."
    assert screen_for_dosage_advice(text, meds, fallback=FALLBACK) == text


def test_dosage_advice_passes_when_no_medications_at_all():
    text = "You could skip it today."
    assert screen_for_dosage_advice(text, [], fallback=FALLBACK) == text


def test_dosage_advice_chinese_phrase_blocked():
    meds = [_med("Donepezil")]
    text = "Donepezil可以不吃。"
    assert screen_for_dosage_advice(text, meds, fallback=FALLBACK) == FALLBACK
