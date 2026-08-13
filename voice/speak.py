"""Text-to-speech for the menu-bar app's spoken responses. Ported from
CampusPilot_v3/voice/speak.py, adapted to reuse this project's shared
openai_client. Falls back to the local macOS `say` command if the OpenAI
TTS call fails, so a network hiccup never leaves the assistant mute."""
import os
import subprocess
import tempfile

from agent.chat import openai_client
from config.settings import settings

OPENAI_VOICE = "onyx"
FALLBACK_VOICE = "Daniel"
FALLBACK_RATE = 175


def _speak_openai(text):

    path = tempfile.mktemp(suffix=".mp3")

    # gpt-4o-mini-tts over tts-1 -- tts-1 has a known issue where it
    # occasionally drifts into another language partway through longer
    # responses (a real, reported OpenAI model quirk, not a bug in this
    # code); gpt-4o-mini-tts doesn't exhibit that.
    with openai_client.audio.speech.with_streaming_response.create(
        model=settings.tts_model,
        voice=OPENAI_VOICE,
        input=text,
        timeout=20
    ) as response:
        response.stream_to_file(path)

    try:
        subprocess.run(["afplay", path], timeout=60)
    finally:
        os.remove(path)


def _speak_fallback(text):

    subprocess.run(
        ["say", "-v", FALLBACK_VOICE, "-r", str(FALLBACK_RATE), text],
        timeout=60
    )


def speak_natural(text):
    """Higher-quality AI voice for the native menu-bar app's spoken
    conversation. For the Streamlit multi-device chat's speak-replies
    checkbox, see agent.tts_control.speak_interruptible instead -- that
    one is instant/free/interruptible via system `say`, which matters more
    there than voice quality does."""

    try:
        _speak_openai(text)
    except Exception:

        try:
            _speak_fallback(text)
        except Exception:
            pass
