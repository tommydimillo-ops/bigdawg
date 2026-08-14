"""Native, always-on wake-word listening for the menu-bar app. Unlike
app.py's browser-based Web Speech API, this samples the real microphone
directly via sounddevice, so it works with no browser tab open at all.

Ported from CampusPilot_v3/voice/listen.py (calibrated volume-threshold
VAD + Whisper transcription), adapted to reuse this project's shared,
retry-wrapped openai_client instead of constructing a second one.
"""
import os
import re
import tempfile
import wave

import numpy as np
import sounddevice as sd

from agent import voice_state
from agent.chat import openai_client
from agent.observability import log_event
from agent.voice_state import VoiceState
from config.settings import settings
from voice import local_transcribe

WAKE_WORD = settings.wake_word.lower()

# Mirrors app.py's browser EXIT_WORDS/isExitPhrase -- ending an active
# conversation always requires addressing Jarvis by name ("Jarvis, that's
# all"), not just any lone word like "stop", so ordinary speech containing
# these words mid-command doesn't accidentally end the conversation.
EXIT_WORDS = re.compile(r"\b(bye|goodbye|stop|close|done|finished|that'?s all)\b", re.IGNORECASE)

# Known empty-audio hallucinations from the transcription model. These
# phrases have appeared verbatim in real menu-bar logs even when nobody
# was speaking, including echoes of the transcription prompt itself.
_HALLUCINATION_FRAGMENTS = (
    "casual spoken request to a personal assistant",
    "may include american slang",
    "filler words and informal phrasing",
)
_HALLUCINATION_ONLY = {
    "context", "context casual", "casual",
}


def is_exit_phrase(text):
    return WAKE_WORD in text.lower() and bool(EXIT_WORDS.search(text))


def strip_wake_word(text):
    """Removes the wake word from anywhere in `text`, leaving whatever's
    left as the actual command -- shared by wait_for_command and
    agent.voice_session's speech-interruption handling so both strip it
    the exact same way."""
    # re.escape -- WAKE_WORD is configurable (settings.wake_word), not
    # guaranteed regex-safe.
    command = re.sub(r"\b" + re.escape(WAKE_WORD) + r"\b", " ", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", command).strip(" ,.:")


def clean_transcript(text):
    """Return a usable transcript or an empty string for model noise.

    Applied centrally so passive wake listening, active follow-ups,
    cancellation listening, and speech interruption all get the same
    protection.
    """
    value = (text or "").strip()
    lowered = value.lower()
    normalized = re.sub(r"[^a-z0-9]+", " ", lowered).strip()
    if not normalized or normalized in _HALLUCINATION_ONLY:
        return ""
    if lowered.lstrip().startswith("context:") or "###" in value:
        return ""
    if any(fragment in lowered for fragment in _HALLUCINATION_FRAGMENTS):
        return ""
    return value


def _select_input_device():
    """The OS's "default" input device can silently become a paired
    iPhone/iPad's mic (Continuity Camera) instead of the Mac's own
    built-in microphone, whenever one happens to be nearby and unlocked
    -- confirmed live: calibration measured near-silence because the
    always-on listener was picking up an iPhone sitting across the room
    rather than the user talking at their laptop, with no error or
    crash to signal it, just a threshold that never gets crossed.
    Re-queried on every call (not cached) so a phone connecting or
    disconnecting mid-session can't leave this pointed at a device that
    no longer makes sense. Falls back to the OS default only if no
    other input device is available at all."""
    try:
        devices = sd.query_devices()
    except Exception:
        return None

    fallback = None
    for i, d in enumerate(devices):
        if d.get("max_input_channels", 0) <= 0:
            continue
        name = d.get("name", "").lower()
        if "iphone" in name or "ipad" in name:
            continue
        if fallback is None:
            fallback = i
        if "macbook" in name or "built-in" in name:
            return i
    return fallback


def listen_for_utterance(
    samplerate=settings.voice_sample_rate,
    max_seconds=None,
    silence_seconds=1.2,
    calibration_seconds=1.0,
    recalibration_seconds=5.0,
    stop_flag=None,
    max_wait_seconds=None,
    on_ready=None,
):
    """Listens quietly (no API calls, no cost) until speech-level volume is
    heard, then records until a stretch of silence follows (or max_seconds
    elapses). Returns (audio, samplerate), or (None, None) if stop_flag was
    set, or max_wait_seconds elapsed, before any speech started.

    on_ready, if given, is called right after initial calibration finishes
    (before the detect-speech loop starts) -- lets a caller that's about to
    make noise of its own (e.g. starting TTS playback) wait for calibration
    against the still-quiet room first, instead of racing it."""

    if max_seconds is None:
        max_seconds = settings.voice_listen_timeout

    chunk_duration = 0.2
    chunk_samples = int(samplerate * chunk_duration)
    silence_chunks_needed = int(silence_seconds / chunk_duration)
    max_chunks = int(max_seconds / chunk_duration)
    calibration_chunks = max(3, int(calibration_seconds / chunk_duration))
    recalibration_chunks = max(1, int(recalibration_seconds / chunk_duration))
    max_wait_chunks = (
        int(max_wait_seconds / chunk_duration) if max_wait_seconds else None
    )

    def _calibrate(stream):

        levels = [
            np.abs(stream.read(chunk_samples)[0]).mean()
            for _ in range(calibration_chunks)
        ]

        # Median, not max -- a single loud spike during calibration (a
        # click, a cough, a fan kicking on) shouldn't be able to set the
        # bar so high that normal speech never crosses it again. Kept
        # deliberately low/sensitive: a missed wake word costs nothing to
        # just repeat, while a threshold even slightly too high makes the
        # assistant silently, permanently deaf until recalibration -- a far
        # worse failure mode than an occasional false trigger (which just
        # transcribes something that doesn't match "jarvis" and is ignored).
        # A steady room (fan/HVAC hum, distant traffic) already sits at a
        # mean-amplitude noise floor of ~150-250 on a laptop's built-in
        # mic, and normal (not shouted) conversational speech at normal
        # distance only reaches roughly 250-350 above that -- a multiplier
        # much above ~1.2x pushes the bar above what real speech actually
        # produces, not just above noise, which is what silently produced
        # the "permanently deaf" failure mode this comment already warned
        # against.
        adaptive = float(np.median(levels)) * 1.15 + 25
        return max(settings.voice_min_signal_level, adaptive)

    input_device = _select_input_device()

    with sd.InputStream(
        samplerate=samplerate, channels=1, dtype="int16", device=input_device,
    ) as stream:

        # Opening/calibrating the microphone is already part of listening.
        # Surface that immediately instead of leaving the menu icon looking
        # idle for the whole calibration window.
        voice_state.set_status(VoiceState.LISTENING)
        speech_threshold = _calibrate(stream)
        log_event(
            "voice_calibrated", component="voice",
            speech_threshold=round(speech_threshold, 1),
            input_device=str(input_device),
        )
        if on_ready is not None:
            on_ready()

        trigger_blocks = []
        chunks_since_calibration = 0
        chunks_waited = 0

        while True:

            if stop_flag is not None and stop_flag.is_set():
                return None, None

            if max_wait_chunks is not None and chunks_waited >= max_wait_chunks:
                return None, None

            block, _ = stream.read(chunk_samples)
            chunks_since_calibration += 1
            chunks_waited += 1

            if np.abs(block).mean() > speech_threshold:
                trigger_blocks.append(block.copy())
                if len(trigger_blocks) >= max(1, settings.voice_trigger_chunks):
                    break
            else:
                trigger_blocks.clear()

            # Nothing's crossed the threshold in a while -- the initial
            # calibration may have been too high. Refresh it periodically
            # so one bad reading can't make listening permanently deaf for
            # the rest of the session.
            if chunks_since_calibration >= recalibration_chunks:
                speech_threshold = _calibrate(stream)
                chunks_since_calibration = 0

        frames = list(trigger_blocks)
        silent_chunks = 0

        for _ in range(max_chunks):

            block, _ = stream.read(chunk_samples)
            frames.append(block.copy())

            if np.abs(block).mean() > speech_threshold:
                silent_chunks = 0
            else:
                silent_chunks += 1

            if silent_chunks >= silence_chunks_needed:
                break

    return np.concatenate(frames, axis=0), samplerate


def transcribe(audio, samplerate):

    voice_state.set_status(VoiceState.TRANSCRIBING)
    descriptor, path = tempfile.mkstemp(suffix=".wav")
    os.close(descriptor)
    try:
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(samplerate)
            wf.writeframes(audio.tobytes())

        try:
            with open(path, "rb") as f:

                # gpt-4o-transcribe over whisper-1 -- meaningfully better on
                # casual/accented real speech in testing. The prompt is a
                # style bias hint (not a strict vocabulary list): it nudges
                # toward hearing "Jarvis" correctly and not over-formalizing
                # casual American slang into stiffer phrasing.
                response = openai_client.audio.transcriptions.create(
                    model=settings.transcription_model,
                    file=f,
                    prompt=(
                        "Casual spoken request to a personal assistant named "
                        "Jarvis. May include American slang, filler words, and "
                        "informal phrasing (e.g. \"gonna\", \"y'all\", \"kinda\")."
                    ),
                    timeout=20
                )
            return clean_transcript(response.text)

        except Exception as error:
            # OpenAI is unreachable, out of quota, or erroring -- fall back
            # to on-device recognition (voice.local_transcribe) rather than
            # going deaf entirely. No network call, no cost, but usually
            # somewhat less accurate than the cloud model, so it's a
            # fallback and not the default.
            log_event(
                "transcription_primary_failed", component="voice",
                level="warning", error_type=type(error).__name__,
            )
            local_text = local_transcribe.transcribe_local(path)
            if local_text is None:
                log_event(
                    "transcription_completely_failed", component="voice",
                    level="error", error_type=type(error).__name__,
                )
                return ""
            log_event("transcription_used_local_fallback", component="voice")
            return clean_transcript(local_text)

    finally:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


def wait_for_command(stop_flag=None):
    """Blocks until an utterance containing the wake word is heard, then
    returns whatever was said around it (may need a follow-up listen if the
    wake word was said alone). Returns None if stop_flag was set first.

    Unlike v3's original (which required "jarvis" as the first word), this
    strips the wake word from anywhere in the utterance -- the app.py
    browser path hit the same "hi Jarvis" bug (greeting said before the
    wake word got silently dropped) and that fix applies here too."""

    while stop_flag is None or not stop_flag.is_set():

        audio, samplerate = listen_for_utterance(stop_flag=stop_flag)

        if audio is None:
            return None

        text = transcribe(audio, samplerate)

        if not text or WAKE_WORD not in text.lower():
            continue

        command = strip_wake_word(text)

        if command:
            return command

        # Treat a deliberate wake word as a greeting. VAD already requires
        # sustained microphone signal and all non-wake transcripts remain
        # ignored, so deleting "Jarvis" here made the most natural way to
        # wake the assistant look completely broken.
        return "hello"

    return None


def listen_for_followup(stop_flag=None, max_wait_seconds=120):
    """For active-conversation mode: listens for the next utterance without
    requiring the wake word again, mirroring app.py's browser passive/active
    split. Returns the transcribed text, '' if speech was heard but empty,
    or None if stop_flag was set or nothing was said within max_wait_seconds
    (the caller should treat None as "drop back to passive/wake-word mode")."""

    # Shorter calibration than the passive wake-word listen (0.6s, the
    # lowest listen_for_utterance's 3-chunk floor allows, vs. the default
    # 1.0s) -- a quick one-word reply like "yes" said right after Jarvis
    # stops talking can be over before a full second of calibration even
    # finishes, so that "yes" never gets heard as speech at all and Jarvis
    # looks like it's ignoring a direct answer to its own question.
    audio, samplerate = listen_for_utterance(
        stop_flag=stop_flag,
        max_wait_seconds=max_wait_seconds,
        calibration_seconds=0.6,
    )

    if audio is None:
        return None

    text = transcribe(audio, samplerate)
    return text or None
