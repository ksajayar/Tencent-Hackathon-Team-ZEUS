import asyncio
import json


class FfmpegError(Exception):
    pass


async def _run(*args: str) -> None:
    # -y: never prompt to overwrite - an unanswered prompt would hang the
    # subprocess forever since we don't attach a stdin pipe.
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        raise FfmpegError(stderr.decode(errors="replace")[:500])


async def probe_duration_seconds(input_path: str) -> float:
    process = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        input_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise FfmpegError(stderr.decode(errors="replace")[:500])
    data = json.loads(stdout)
    return float(data["format"]["duration"])


async def preprocess_for_stt(input_path: str, output_path: str) -> None:
    """§06 §6.1: normalise to 16kHz mono WAV. Gentle denoise + loudness
    normalisation, not aggressive noise suppression - over-filtering a soft,
    distant elderly voice into silence is the failure mode that actually
    hurts here, not background noise confusing the model."""
    await _run(
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-ar",
        "16000",
        "-ac",
        "1",
        "-af",
        "highpass=f=80,lowpass=f=8000,afftdn=nf=-25,loudnorm=I=-16:TP=-1.5:LRA=11",
        "-f",
        "wav",
        output_path,
    )


async def transcode_tts_to_ogg(input_path: str, output_path: str) -> None:
    """§06 §6.2 / CHANNEL-3: edge-tts outputs MP3; only OGG/Opus renders as a
    WhatsApp voice note rather than a downloadable file attachment."""
    await _run(
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-c:a",
        "libopus",
        "-b:a",
        "24k",
        "-ar",
        "48000",
        "-ac",
        "1",
        output_path,
    )
