"""§17 set medication moved from a fixed 4-option menu to free text, parsed
by gemini_client.parse_medication_schedule() - see the module comment there
for why this is safe: the parsed RRULE is validated against a strict shape
(_SCHEDULE_RRULE_RE) before it can reach a caller at all, and the caller
(caregiver.py) never writes from the parse alone - it echoes label_en/
label_zh back to the caregiver and only writes on an explicit yes.

This covers the two deterministic backstops directly: the RRULE shape
validator (nothing malformed or out of range can pass), and
_render_instruction building the stored instruction from the already-
confirmed labels rather than re-deriving or re-parsing anything.
"""

from app.ai.gemini_client import _parse_medication_schedule_json
from app.pipelines.caregiver import _render_instruction


def _raw(
    rrule: str,
    label_en: str = "once a day, in the morning",
    label_zh: str = "每天一次，早上",
    parseable: bool = True,
) -> str:
    import json

    return json.dumps(
        {"parseable": parseable, "rrule": rrule, "label_en": label_en, "label_zh": label_zh}
    )


def test_valid_single_time_schedule_parses():
    result = _parse_medication_schedule_json(_raw("FREQ=DAILY;BYHOUR=8;BYMINUTE=0"))
    assert result == {
        "rrule": "FREQ=DAILY;BYHOUR=8;BYMINUTE=0",
        "label_en": "once a day, in the morning",
        "label_zh": "每天一次，早上",
    }


def test_valid_multi_time_schedule_parses():
    result = _parse_medication_schedule_json(_raw("FREQ=DAILY;BYHOUR=8,13,20;BYMINUTE=0"))
    assert result["rrule"] == "FREQ=DAILY;BYHOUR=8,13,20;BYMINUTE=0"


def test_exact_caregiver_given_time_is_accepted():
    result = _parse_medication_schedule_json(_raw("FREQ=DAILY;BYHOUR=7;BYMINUTE=30"))
    assert result["rrule"] == "FREQ=DAILY;BYHOUR=7;BYMINUTE=30"


def test_marked_unparseable_returns_none():
    assert (
        _parse_medication_schedule_json(_raw("FREQ=DAILY;BYHOUR=8;BYMINUTE=0", parseable=False))
        is None
    )


def test_malformed_json_returns_none():
    assert _parse_medication_schedule_json("not json at all") is None


def test_markdown_fenced_json_is_still_parsed():
    raw = "```json\n" + _raw("FREQ=DAILY;BYHOUR=8;BYMINUTE=0") + "\n```"
    result = _parse_medication_schedule_json(raw)
    assert result is not None


def test_out_of_range_hour_is_rejected():
    # 24 is not a valid hour - the RRULE shape guard must catch a model
    # hallucinating something outside the domain, not just malformed JSON.
    assert _parse_medication_schedule_json(_raw("FREQ=DAILY;BYHOUR=24;BYMINUTE=0")) is None


def test_out_of_range_minute_is_rejected():
    assert _parse_medication_schedule_json(_raw("FREQ=DAILY;BYHOUR=8;BYMINUTE=60")) is None


def test_wrong_freq_is_rejected():
    # Only FREQ=DAILY is ever valid here - a weekly/monthly rule would not
    # match sync_medication_reminders()'s daily-firing assumption.
    assert _parse_medication_schedule_json(_raw("FREQ=WEEKLY;BYHOUR=8;BYMINUTE=0")) is None


def test_missing_label_is_rejected_even_with_a_valid_rrule():
    # A valid RRULE with no label would break the confirm-echo step, the
    # actual safety mechanism here - both must be present together.
    assert (
        _parse_medication_schedule_json(_raw("FREQ=DAILY;BYHOUR=8;BYMINUTE=0", label_en="")) is None
    )


def test_extra_trailing_content_after_valid_rrule_is_rejected():
    # Anchored regex - "FREQ=DAILY;BYHOUR=8;BYMINUTE=0; DROP TABLE" or any
    # trailing garbage must not slip through on a prefix match.
    assert _parse_medication_schedule_json(_raw("FREQ=DAILY;BYHOUR=8;BYMINUTE=0;EXTRA=1")) is None


def test_render_instruction_uses_the_confirmed_labels_directly():
    instruction_en, instruction_zh = _render_instruction(
        {
            "dose_text": "1 tablet",
            "schedule_label_en": "once a day, in the morning, before food",
            "schedule_label_zh": "每天早上一次，饭前",
        }
    )
    assert instruction_en == "Take 1 tablet, once a day, in the morning, before food."
    assert instruction_zh == "每天早上一次，饭前服用1 tablet。"
