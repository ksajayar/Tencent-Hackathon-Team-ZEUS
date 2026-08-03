import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.document import Document


async def create_document(
    session: AsyncSession,
    *,
    media_id: uuid.UUID | None = None,
    doc_kind: str,
    extracted_text: str | None,
    summary_en: str | None,
    summary_zh: str | None,
    was_scanned: bool,
    patient_id: uuid.UUID | None = None,
) -> Document:
    """`media_id` is optional (§17 set bloodwork's plain-text case has no
    upload behind it); `patient_id` likewise (only a caregiver upload needs
    it - see app/db/models/document.py). Every existing caller omits
    `patient_id` and passes `media_id` positionally-by-keyword as before, so
    neither addition changes their behavior."""
    document = Document(
        media_id=media_id,
        patient_id=patient_id,
        doc_kind=doc_kind,
        extracted_text=extracted_text,
        summary_en=summary_en,
        summary_zh=summary_zh,
        was_scanned=was_scanned,
    )
    session.add(document)
    await session.flush()
    return document


async def get_latest_bloodwork(session: AsyncSession, patient_id: uuid.UUID) -> Document | None:
    """§17 §7 patient-side reads: deterministic, no LLM - the same reasoning
    as _medication_query (app/pipelines/text.py). Bloodwork text is
    unstructured free text (from OCR or a caregiver's own typing) and could
    say anything; handing it to a free-text QA path would open a new,
    unguarded hallucination surface right where this session's whole SAFETY-1
    pass just closed one for medications - so this is read-and-render only,
    never fed into build_context() for the general-conversation LLM path."""
    result = await session.execute(
        select(Document)
        .where(Document.patient_id == patient_id, Document.doc_kind == "blood_work")
        .order_by(Document.created_at.desc())
        .limit(1)
    )
    return result.scalars().first()


# Deliberately narrow - "A", "B", "AB", "O" with an optional +/-, immediately
# after the English or Chinese word for "blood type". A caregiver's typed
# bloodwork or an OCR'd lab report is the only source; if the phrase isn't
# there in that shape, this returns None rather than guess.
#
# The separator between the label and the letter is an explicit
# `\s*[:\-]?\s*` (whitespace + optional colon/dash), not `\D{0,N}` - `\D`
# matches letters too, and greedy backtracking made an earlier version of
# this pattern consume the "A" of "AB" as filler, silently returning "B-"
# for "Blood Type AB-" (caught by testing this against real strings, not
# obvious from reading the pattern).
_BLOOD_TYPE_RE = re.compile(
    r"blood\s*type\s*[:\-]?\s*(AB|A|B|O)\s*([+-])?|血型\s*[:：]?\s*(AB|A|B|O)\s*型?\s*([+-])?",
    re.IGNORECASE,
)


def extract_blood_type(document: Document | None) -> str | None:
    """Regex extraction over stored text, not an LLM call - same
    deterministic-only reasoning as get_latest_bloodwork above. Checked
    against whichever of summary_en/summary_zh/extracted_text actually has
    content, since a text-intake or image-intake bloodwork entry may have
    only extracted_text and no summary."""
    if document is None:
        return None
    haystack = " ".join(
        part for part in (document.summary_en, document.summary_zh, document.extracted_text) if part
    )
    match = _BLOOD_TYPE_RE.search(haystack)
    if not match:
        return None
    letters = match.group(1) or match.group(3)
    sign = match.group(2) or match.group(4) or ""
    return f"{letters.upper()}{sign}"
