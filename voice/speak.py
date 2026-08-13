"""Text-to-speech for the menu-bar app's spoken responses. Ported from
CampusPilot_v3/voice/speak.py, adapted to reuse this project's shared
openai_client. Falls back to the local macOS `say` command if the OpenAI
TTS call fails, so a network hiccup never leaves the assistant mute.

Playback (Phase 6) is tracked via agent.tts_control's shared PID file, the
same mechanism the Streamlit multi-device chat's speak_interruptible
already used -- so a wake word heard while Jarvis is talking can cut
either kind of speech off via the one stop_speaking() call, rather than
this module needing its own separate, duplicate interruption path."""
import os
import subprocess
import tempfile

from agent import tts_control
from agent.chat import openai_client
from config.settings import settings

OPENAI_VOICE = "onyx"
FALLBACK_VOICE = "Daniel"
FALLBACK_RATE = 175


def _play_and_track(command):
    """Launches `command` (already resolved to argv) without blocking the
    caller from also tracking it, then waits for it to finish -- a plain
    subprocess.run() would block just the same in the normal case, but
    wouldn't let an external stop_speaking() call (e.g. from a
    concurrently-listening wake-word watcher) cut it short."""
    process = subprocess.Popen(command)
    tts_control.track_pid(process.pid)
    process.wait(timeout=60)


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
        _play_and_track(["afplay", path])
    finally:
        os.remove(path)


def _speak_fallback(text):

    _play_and_track(["say", "-v", FALLBACK_VOICE, "-r", str(FALLBACK_RATE), text])


def speak_natural(text):
    """Higher-quality AI voice for the native menu-bar app's spoken
    conversation. For the Streamlit multi-device chat's speak-replies
    checkbox, see agent.tts_control.speak_interruptible instead -- that
    one is instant/free/interruptible via system `say`, which matters more
    there than voice quality does.

    A no-op if settings.tts_enabled is False -- the one place that check
    lives, rather than every call site needing to know about it."""

    if not settings.tts_enabled or not text:
        return

    try:
        _speak_openai(text)
    except Exception:

        try:
            _speak_fallback(text)
        except Exception:
            pass
