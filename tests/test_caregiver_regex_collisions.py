"""§11 priority test 4 - the 5 caregiver command trigger regexes, both
languages, confirmed non-colliding with the patient-side query regexes in
text.py.

`handle()` (app/pipelines/text.py) runs caregiver.handle_command() before
falling through to _MEDICATION_QUERY_RE / _BLOOD_TYPE_QUERY_RE / etc, so a
caregiver's "set medication" must never also satisfy the query regex (it
would still resolve correctly today because handle_command runs first and
short-circuits, but a collision would make the ordering load-bearing in a
way that's easy to break by refactoring) - and, in the other direction, an
ordinary patient query must never accidentally trigger a caregiver `set`
command if the caregiver dispatcher is ever reached on a query-shaped
string.
"""

from app.pipelines.caregiver import (
    _CHECK_CANDIDATES_RE,
    _SET_ADDRESS_RE,
    _SET_APPOINTMENT_RE,
    _SET_BLOODWORK_RE,
    _SET_MEDICATION_RE,
)
from app.pipelines.text import (
    _BLOOD_TYPE_QUERY_RE,
    _BLOODWORK_QUERY_RE,
    _HOME_ADDRESS_QUERY_RE,
    _MEDICATION_QUERY_RE,
)

_TRIGGER_CASES = [
    (_SET_APPOINTMENT_RE, ["set appointment", "add appointment", "Set Appointment for mum"]),
    (_SET_APPOINTMENT_RE, ["设置预约", "新增预约", "添加预约"]),
    (_SET_BLOODWORK_RE, ["set bloodwork", "add bloodwork"]),
    (_SET_BLOODWORK_RE, ["设置验血", "添加验血"]),
    (_SET_ADDRESS_RE, ["set address"]),
    (_SET_ADDRESS_RE, ["设置地址"]),
    (_SET_MEDICATION_RE, ["set medication", "add medication"]),
    (_SET_MEDICATION_RE, ["设置用药", "添加用药"]),
    (_CHECK_CANDIDATES_RE, ["check candidates", "check candidate"]),
    (_CHECK_CANDIDATES_RE, ["查看待审核", "查看候选"]),
]


def test_each_trigger_regex_matches_its_own_phrases_both_languages():
    for pattern, phrases in _TRIGGER_CASES:
        for phrase in phrases:
            assert pattern.search(phrase), f"{pattern.pattern!r} did not match {phrase!r}"


_QUERY_PHRASES = {
    "medication query": (_MEDICATION_QUERY_RE, ["what medicine", "my medication", "吃什么药"]),
    "blood type query": (_BLOOD_TYPE_QUERY_RE, ["blood type", "my blood type", "血型"]),
    "bloodwork query": (_BLOODWORK_QUERY_RE, ["my bloodwork", "blood test", "验血"]),
    "home address query": (_HOME_ADDRESS_QUERY_RE, ["where's my home", "我家在哪"]),
}

_CAREGIVER_TRIGGER_PATTERNS = [
    _SET_APPOINTMENT_RE,
    _SET_BLOODWORK_RE,
    _SET_ADDRESS_RE,
    _SET_MEDICATION_RE,
    _CHECK_CANDIDATES_RE,
]


def test_set_medication_does_not_satisfy_the_medication_query_regex():
    assert not _MEDICATION_QUERY_RE.search("set medication")
    assert not _MEDICATION_QUERY_RE.search("add medication")
    assert not _MEDICATION_QUERY_RE.search("设置用药")
    assert not _MEDICATION_QUERY_RE.search("添加用药")


def test_set_address_does_not_satisfy_the_home_address_query_regex():
    assert not _HOME_ADDRESS_QUERY_RE.search("set address")
    assert not _HOME_ADDRESS_QUERY_RE.search("设置地址")


def test_set_bloodwork_english_does_not_satisfy_the_bloodwork_query_regex():
    assert not _BLOODWORK_QUERY_RE.search("set bloodwork")
    assert not _BLOODWORK_QUERY_RE.search("add bloodwork")


def test_set_bloodwork_chinese_does_collide_with_the_query_regex_known_landmine():
    """Real, verified overlap: _BLOODWORK_QUERY_PHRASES includes the bare
    "验血" (no word-boundary concept in Chinese), which is a substring of
    "设置验血"/"添加验血" ("set/add bloodwork"). This is harmless *today*
    only because handle() runs caregiver.handle_command() - whose
    _SET_BLOODWORK_RE also matches first and short-circuits - before ever
    reaching _BLOODWORK_QUERY_RE (text.py's caregiver branch, the `elif`
    chain after `command_reply`). If a future change ever lets a "设置验血"
    message reach _BLOODWORK_QUERY_RE first (reordering the elif chain, or
    handle_command legitimately returning None for it, e.g. mid a
    different pending flow), a caregiver trying to *set* bloodwork would
    instead get back their patient's *existing* bloodwork result. Asserting
    the collision here, not its absence, so a future reader sees the real
    hazard instead of a false safety guarantee.
    """
    assert _BLOODWORK_QUERY_RE.search("设置验血")
    assert _BLOODWORK_QUERY_RE.search("添加验血")


def test_ordinary_patient_queries_do_not_trigger_any_caregiver_set_command():
    for _name, (_query_re, phrases) in _QUERY_PHRASES.items():
        for phrase in phrases:
            for trigger_re in _CAREGIVER_TRIGGER_PATTERNS:
                assert not trigger_re.search(phrase), (
                    f"caregiver trigger {trigger_re.pattern!r} unexpectedly matched "
                    f"patient query phrase {phrase!r}"
                )


def test_caregiver_trigger_regexes_are_mutually_exclusive_on_their_own_phrases():
    for i, (pattern, phrases) in enumerate(_TRIGGER_CASES):
        others = [p for j, (p, _) in enumerate(_TRIGGER_CASES) if j != i]
        for phrase in phrases:
            for other in others:
                if other is pattern:
                    continue
                assert not other.search(phrase), (
                    f"{other.pattern!r} unexpectedly also matched {phrase!r} "
                    f"(owned by {pattern.pattern!r})"
                )
