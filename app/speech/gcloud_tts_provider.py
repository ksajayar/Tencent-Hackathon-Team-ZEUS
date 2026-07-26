class GoogleCloudTTSProvider:
    """Stub fallback (§06 §6.2). If edge-tts breaks the week of the demo,
    this is where a real implementation would go - a config flip rather than
    a rewrite, per TTSProvider being an interface. Not built for the demo:
    it needs its own GCP service-account credentials, separate from the
    OAuth-based Gmail/Calendar integration this project otherwise uses."""

    async def synthesize_mp3(self, text: str, *, voice: str, rate: str, output_path: str) -> None:
        raise NotImplementedError("Google Cloud TTS fallback not implemented - see docs §06 §6.2")
