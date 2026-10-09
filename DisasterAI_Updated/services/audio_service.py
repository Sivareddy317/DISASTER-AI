import os
from pathlib import Path
from typing import Optional

_model = None
_model_failed = False


def get_model():
    global _model, _model_failed

    if _model_failed:
        return None

    if _model is None:
        try:
            from faster_whisper import WhisperModel
            print("Loading speech-to-text model (faster-whisper base)...")
            _model = WhisperModel(
                "base",
                device="cpu",
                compute_type="int8"
            )
            print("Speech-to-text model loaded successfully.")
        except Exception as exc:
            print(f"Warning: Failed to load speech-to-text model: {exc}")
            _model_failed = True
            return None

    return _model


def transcribe_audio(audio_path: str, task: str = "translate") -> str:
    """
    Transcribes and optionally translates an audio file using faster-whisper.
    Uses task="translate" by default to translate regional Indian languages (e.g., Telugu) into English,
    enabling downstream AI disaster extraction.
    """
    path = Path(audio_path)

    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    if path.suffix.lower() not in {".mp3", ".wav", ".m4a", ".ogg"}:
        raise ValueError("Unsupported audio format. Supported formats: MP3, WAV, M4A, OGG.")

    if path.stat().st_size == 0:
        return ""

    # Allow mock mode for tests or when requested
    if os.getenv("MOCK_TRANSCRIPTION", "false").lower() == "true":
        return f"[MOCK TRANSCRIPT] Emergency call from audio {path.name}"

    model = get_model()
    if model is None:
        raise RuntimeError(
            "Speech-to-text model is not available. Please verify faster-whisper and ctranslate2 installation."
        )

    try:
        segments, info = model.transcribe(
            str(path),
            beam_size=1,
            task=task,
            vad_filter=True
        )

        text_parts = []
        for segment in segments:
            text = segment.text.strip()
            if text:
                text_parts.append(text)

        transcript = " ".join(text_parts).strip()
        return transcript

    except Exception as exc:
        raise RuntimeError(f"Transcription failed for {path.name}: {exc}") from exc