from typing import NamedTuple


class Reply(NamedTuple):
    """A reply, plus the optional `meta` to attach to the outbound message it
    becomes. `meta` carries pending-state flags that the next inbound message
    resolves - the two-step contact chain (§07 §7.11) and the caregiver
    command flows (§17) both use this. Shared between text.py and
    pipelines/caregiver.py so a caregiver flow's prompt can carry meta the
    same way _confirm_contact_question's can, without those two modules
    importing each other's Reply definitions."""

    text: str
    meta: dict | None = None
