"""Edward's voice stack, pure parts.

Everything in this package is I/O-free and unit-tested: wire encoders and
parsers for ElevenLabs Scribe (STT) and stream-input (TTS), the sentence
feeder that turns LLM tokens into speakable chunks, the spoken bridges,
the session reducer, and voice-memo helpers. The sockets live in
``services/voice_service.py`` and ``routers/voice_ws.py``.
"""
