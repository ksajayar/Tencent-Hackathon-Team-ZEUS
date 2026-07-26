import asyncio
import io

from PIL import Image, ImageOps

# §06 §6.3: downscale so the long edge is <=1568px - larger costs tokens and
# adds nothing Gemini can use.
MAX_LONG_EDGE = 1568
JPEG_QUALITY = 85


def _preprocess_sync(content: bytes) -> bytes:
    """Auto-rotate from EXIF, then strip it (it carries GPS) by re-encoding
    into a fresh JPEG that never copies the original's metadata - simpler
    and more reliable than deleting individual EXIF tags. Deliberately no
    binarisation/CLAHE step: Gemini reads the original better than a
    thresholded bitmap (§06 §6.3), unlike classical OCR."""
    with Image.open(io.BytesIO(content)) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.thumbnail((MAX_LONG_EDGE, MAX_LONG_EDGE), Image.LANCZOS)
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=JPEG_QUALITY)
        return buffer.getvalue()


async def preprocess(content: bytes) -> bytes:
    return await asyncio.to_thread(_preprocess_sync, content)
