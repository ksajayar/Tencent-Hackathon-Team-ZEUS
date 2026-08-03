"""§11 priority test 3 - schedule-menu RRULE round trip.

`set medication` offers a fixed schedule menu rather than free-text RRULE
parsing. `_menu_key_for` must be able to map every RRULE string the menu
itself produces back to its menu key - it's the only way
`_render_instruction` finds the right bilingual label after the caregiver
has already picked a number. A mismatch here would silently render the
wrong "once a day" / "twice a day" wording for a saved medication.
"""

import pytest

from app.pipelines.caregiver import _SCHEDULE_MENU, _menu_key_for, _render_instruction


@pytest.mark.parametrize("key", list(_SCHEDULE_MENU.keys()))
def test_menu_key_round_trips_through_its_own_rrule(key):
    rrule, _label = _SCHEDULE_MENU[key]
    assert _menu_key_for(rrule) == key


def test_unknown_rrule_raises_rather_than_silently_mismatching():
    with pytest.raises(ValueError):
        _menu_key_for("FREQ=WEEKLY;BYDAY=MO")


def test_render_instruction_english_and_chinese_for_each_menu_option():
    expected = {
        "1": ("once a day, morning", "每天一次，早上"),
        "2": ("once a day, evening", "每天一次，晚上"),
        "3": ("twice a day, morning and evening", "每天两次，早晚各一次"),
        "4": ("three times a day, with meals", "每天三次，三餐时"),
    }
    for key, (rrule, _label) in _SCHEDULE_MENU.items():
        instruction_en, instruction_zh = _render_instruction(
            {"dose_text": "1 tablet", "schedule_rrule": rrule}
        )
        label_en, label_zh = expected[key]
        assert instruction_en == f"Take 1 tablet, {label_en}."
        assert instruction_zh == f"{label_zh}服用1 tablet。"


def test_twice_daily_rrule_has_both_byhour_values():
    rrule, _label = _SCHEDULE_MENU["3"]
    assert "BYHOUR=8,20" in rrule


def test_three_times_daily_rrule_has_three_byhour_values():
    rrule, _label = _SCHEDULE_MENU["4"]
    assert "BYHOUR=8,13,20" in rrule
