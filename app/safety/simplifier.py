from app.core.logging import get_logger

logger = get_logger(__name__)

MAX_REPLY_CHARS = 600

_SENTENCE_ENDINGS = ("。", "！", "？", ". ", "! ", "? ")


def simplify(text: str) -> str:
    """Post-generation guard: caps reply length, breaking at a sentence boundary
    rather than mid-word, so a verbose model response still reads naturally to
    someone with dementia.
    """
    if len(text) <= MAX_REPLY_CHARS:
        return text

    truncated = text[:MAX_REPLY_CHARS]
    for punct in _SENTENCE_ENDINGS:
        idx = truncated.rfind(punct)
        if idx != -1:
            truncated = truncated[: idx + len(punct)].rstrip()
            break
    else:
        truncated = truncated.rstrip() + "…"

    logger.warning("simplifier_truncated", original_len=len(text), final_len=len(truncated))
    return truncated
