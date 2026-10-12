"""
Voice API router for Edward.

Provides endpoints for voice services:
- POST /api/voice/transcribe: Audio to text only
- POST /api/voice/synthesize: Text to audio only
- GET /api/voice/status: Voice service status
"""

import logging

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse, Response

from services.voice_service import get_voice_service, get_status as get_voice_status
from services.skills_service import is_skill_enabled

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/voice/status")
async def voice_status():
    """Get voice service status."""
    enabled = await is_skill_enabled("voice")
    status_info = get_voice_status()

    return {
        "enabled": enabled,
        **status_info,
    }


@router.post("/voice/transcribe")
async def transcribe_audio(
    audio: UploadFile = File(...),
    language: str = Form(default="en"),
):
    """
    Transcribe audio to text using ElevenLabs Scribe v2.

    Args:
        audio: Audio file (webm, wav, mp3, m4a, ogg)
        language: Language code (default: "en")

    Returns:
        {"text": "transcribed text", "language": "eng"}
    """
    # Check if voice skill is enabled
    if not await is_skill_enabled("voice"):
        raise HTTPException(status_code=403, detail="Voice skill is not enabled")

    try:
        voice_service = get_voice_service()
        audio_bytes = await audio.read()

        result = await voice_service.transcribe(
            audio_bytes=audio_bytes,
            filename=audio.filename or "audio.webm",
            mime_type=audio.content_type,
            language=language,
        )

        return {"text": result.text, "language": result.language}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/voice/synthesize")
async def synthesize_speech(
    text: str = Form(...),
    stream: bool = Form(default=True),
):
    """
    Synthesize text to speech using ElevenLabs.

    Args:
        text: Text to synthesize
        stream: Whether to stream the response (default: True)

    Returns:
        Audio file (mp3 format)
    """
    # Check if voice skill is enabled
    if not await is_skill_enabled("voice"):
        raise HTTPException(status_code=403, detail="Voice skill is not enabled")

    try:
        voice_service = get_voice_service()

        if stream:
            # Stream audio chunks
            async def generate():
                async for chunk in voice_service.synthesize_stream(text):
                    yield chunk

            return StreamingResponse(
                generate(),
                media_type="audio/mpeg",
                headers={
                    "Content-Disposition": "inline; filename=response.mp3",
                },
            )
        else:
            # Return full audio
            audio_bytes = await voice_service.synthesize(text)
            return Response(
                content=audio_bytes,
                media_type="audio/mpeg",
                headers={
                    "Content-Disposition": "inline; filename=response.mp3",
                },
            )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
