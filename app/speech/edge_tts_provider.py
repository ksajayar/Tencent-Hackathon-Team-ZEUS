import edge_tts


class EdgeTTSProvider:
    """§06 §6.2: free, no key, no quota, best Mandarin voices at zero cost.
    Unofficial - reverse-engineers a Microsoft Edge endpoint - so it's the
    single most likely third-party failure in the system; that's exactly why
    TTSProvider is an interface and not a direct dependency elsewhere."""

    async def synthesize_mp3(self, text: str, *, voice: str, rate: str, output_path: str) -> None:
        communicate = edge_tts.Communicate(text, voice, rate=rate)
        await communicate.save(output_path)


provider = EdgeTTSProvider()
