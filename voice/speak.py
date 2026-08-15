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
import re
import subprocess
import tempfile
import time

from agent import tts_control
from agent.chat import openai_client
from agent.observability import log_event, preview
from agent.request_context import get_current_request_id
from agent.usage import record_llm_usage
from config.settings import settings

OPENAI_VOICE = "onyx"
FALLBACK_VOICE = "Daniel"
FALLBACK_RATE = 175

# Splits after a ., !, or ? that's followed by whitespace and then a
# capital letter/digit/quote -- good enough for TTS pacing (occasionally
# splits after an abbreviation like "Dr." into its own short utterance;
# never splits a decimal like "3.5", since that has no whitespace after
# the period for this pattern to match). Doesn't need to be perfect: the
# full concatenation of chunks is always exactly the original text either
# way, so a slightly-off boundary only affects pacing, never content.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])")


def _split_into_sentences(text):
    return [s for s in _SENTENCE_BOUNDARY.split(text.strip()) if s]


def _play_and_track(command):
    """Launches `command` (already resolved to argv) without blocking the
    caller from also tracking it, then waits for it to finish -- a plain
    subprocess.run() would block just the same in the normal case, but
    wouldn't let an external stop_speaking() call (e.g. from a
    concurrently-listening wake-word watcher) cut it short.

    Returns the process's exit code: negative if it was killed by a
    signal (stop_speaking() sends SIGTERM), so a caller iterating
    multiple chunks can tell "finished normally" apart from "was
    interrupted" and stop moving on to the next chunk in the latter
    case."""
    process = subprocess.Popen(command)
    tts_control.track_pid(process.pid)
    try:
        process.wait(timeout=60)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise
    finally:
        tts_control.untrack_pid(process.pid)
    return process.returncode


def _speak_openai_chunk(text):
    """Synthesizes and plays a single chunk. Returns True if playback was
    cut short by an interruption, so the caller knows to stop rather than
    speaking the next chunk right after something already asked Jarvis to
    stop."""

    descriptor, path = tempfile.mkstemp(suffix=".mp3")
    os.close(descriptor)
    try:
        # gpt-4o-mini-tts over tts-1 -- tts-1 has a known issue where it
        # occasionally drifts into another language partway through longer
        # responses (a real, reported OpenAI model quirk, not a bug in this
        # code); gpt-4o-mini-tts doesn't exhibit that.
        _call_started = time.time()
        with openai_client.audio.speech.with_streaming_response.create(
            model=settings.tts_model,
            voice=OPENAI_VOICE,
            input=text,
            timeout=20
        ) as response:
            response.stream_to_file(path)
        # Billed by input character count, not tokens -- by the time
        # speech_natural() runs, execute_task_stream's own request_id
        # binding has already been unbound (the reply was fully
        # generated first), so request_id is typically None here too;
        # still correctly bucketed under operation="tts". Summed across
        # every chunk of one reply this totals the same cost a single
        # call for the whole text would have -- OpenAI TTS bills by
        # character count, chunking doesn't change that.
        record_llm_usage(
            provider="openai", model=settings.tts_model, operation="tts",
            request_id=get_current_request_id(), character_count=len(text),
            duration_seconds=time.time() - _call_started,
        )

        returncode = _play_and_track(["afplay", path])
        return returncode is not None and returncode < 0
    finally:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def _speak_openai(text):
    """Synthesizes and plays sentence-by-sentence instead of one call for
    the whole response. By the time execute_task_stream's text has
    finished streaming, Jarvis already knows the complete reply -- the
    only latency left before he starts talking used to be a single
    OpenAI TTS network round-trip sized to the ENTIRE response. Chunking
    means the first (much shorter, much faster) sentence's TTS call is
    what gates when speech actually starts, not the whole reply's -- a
    real cut to that gap for anything longer than one sentence. A short,
    single-sentence reply (the common case for quick voice exchanges)
    produces exactly one chunk, so this is a no-op change for those."""

    for sentence in _split_into_sentences(text):
        if _speak_openai_chunk(sentence):
            break


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
    except Exception as primary_error:

        log_event(
            "tts_primary_failed", component="voice", level="warning",
            error_type=type(primary_error).__name__, text_preview=preview(text),
        )

        try:
            _speak_fallback(text)
        except Exception as fallback_error:
            # Both paths failed -- Jarvis is now silently mute. This is the
            # one place that outcome is otherwise invisible (no exception
            # propagates, no caller checks a return value), so it's the one
            # place that must not just swallow it silently.
            log_event(
                "tts_completely_failed", component="voice", level="error",
                primary_error_type=type(primary_error).__name__,
                fallback_error_type=type(fallback_error).__name__,
                text_preview=preview(text),
            )
