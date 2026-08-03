import asyncio
import io
import re

import pypdf

# Collapses pypdf's raw extract_text() output into one readable block.
# pypdf wraps at the PDF's physical line boundaries, not sentence or word
# boundaries, so a single sentence routinely comes back split mid-word
# across several lines with irregular spacing - shown verbatim to a
# dementia patient (this is the degraded-mode fallback, sent unsummarised),
# that reads as broken text rather than a readable sentence. Purely
# mechanical whitespace reflow, never touches which characters are present
# (SAFETY-1: this stays a verbatim transcript, never rephrased).
_WHITESPACE_RUN_RE = re.compile(r"\s+")


def _normalize_extracted_text(text: str) -> str:
    return _WHITESPACE_RUN_RE.sub(" ", text).strip()


# §03 §3.3 / §05 §5.4: Twilio's own cap and the page-count cap.
MAX_PDF_PAGES = 30
TEXT_PROBE_MIN_CHARS = 100
PROBE_PAGE_LIMIT = 5  # enough to tell scanned vs text-native without reading the whole doc


def _probe_sync(content: bytes) -> dict:
    reader = pypdf.PdfReader(io.BytesIO(content))
    page_count = len(reader.pages)
    probe_text = "".join(page.extract_text() or "" for page in reader.pages[:PROBE_PAGE_LIMIT])
    was_scanned = len(probe_text.strip()) < TEXT_PROBE_MIN_CHARS
    return {"page_count": page_count, "was_scanned": was_scanned}


async def probe(content: bytes) -> dict:
    """§05 §5.4: page count + a text-extractability probe. Gemini reads
    scanned pages as images either way - this only decides which path was
    taken, for the reply/log, not whether processing can proceed."""
    return await asyncio.to_thread(_probe_sync, content)


def _extract_first_paragraph_sync(content: bytes) -> str:
    reader = pypdf.PdfReader(io.BytesIO(content))
    text = (reader.pages[0].extract_text() or "") if reader.pages else ""
    paragraph = text.strip().split("\n\n")[0].strip()
    return _normalize_extracted_text(paragraph)[:500]


async def extract_first_paragraph(content: bytes) -> str:
    """Degraded-mode fallback (§05 §5.4): when Gemini is unavailable, return
    the first paragraph of extractable text unsummarised rather than nothing.
    Empty on a scanned PDF with no text layer - callers handle that case."""
    return await asyncio.to_thread(_extract_first_paragraph_sync, content)
