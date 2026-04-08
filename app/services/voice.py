import io
import tempfile

import structlog
from openai import OpenAI
from pydub import AudioSegment

from app.config import settings
from app.services.telegram import telegram_service

logger = structlog.get_logger()


async def transcribe_voice_message(audio_id: str) -> dict:
    """Download voice note from WhatsApp and transcribe with Whisper."""
    if not settings.groq_api_key:
        return {"success": False, "error": "Voice transcription not configured (missing Groq API key)"}

    try:
        # Download audio from Telegram
        audio_bytes = await telegram_service.download_voice(audio_id)

        # Convert OGG/Opus to WAV using pydub
        audio = AudioSegment.from_file(io.BytesIO(audio_bytes), format="ogg")

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
            audio.export(tmp.name, format="wav")

            # Transcribe with Whisper via Groq
            client = OpenAI(
                api_key=settings.groq_api_key,
                base_url="https://api.groq.com/openai/v1",
            )
            with open(tmp.name, "rb") as audio_file:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language="en",
                )

        return {
            "success": True,
            "text": transcript.text,
        }

    except Exception as e:
        logger.exception("Failed to transcribe voice message", error=str(e))
        return {"success": False, "error": f"Transcription failed: {str(e)}"}
