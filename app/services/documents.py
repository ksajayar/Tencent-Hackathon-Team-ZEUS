import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.document import Document


async def create_document(
    session: AsyncSession,
    *,
    media_id: uuid.UUID,
    doc_kind: str,
    extracted_text: str | None,
    summary_en: str | None,
    summary_zh: str | None,
    was_scanned: bool,
) -> Document:
    document = Document(
        media_id=media_id,
        doc_kind=doc_kind,
        extracted_text=extracted_text,
        summary_en=summary_en,
        summary_zh=summary_zh,
        was_scanned=was_scanned,
    )
    session.add(document)
    await session.flush()
    return document
