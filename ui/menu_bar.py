"""Native macOS menu-bar presence for CampusPilot -- an always-on Jarvis
that doesn't depend on a browser tab being open. Ported from
CampusPilot_v3/ui/menu_bar.py (the rumps app shell, background-thread/
main-thread event-queue pattern, and native confirm/notification UX), but
wired to *this* project's existing, tested backend --
agent.executor.execute_task_stream (via agent.voice_session) -- instead of
v3's own agent engine, so it inherits the same tool set, permission
levels, and audit logging as the Streamlit app rather than a second,
separately-secured code path.

Phase 6 turned voice into a real interface to that same core: requests
still only ever flow through agent.voice_session.run_request(), which
calls execute_task_stream() exactly like chat/dashboard/scheduled tasks
do -- nothing here makes its own tool-calling or permission decisions.
This file's job is strictly rumps lifecycle glue (menu items, thread
management, translating voice/execution state into the menu-bar title and
notifications) -- the actual conversational orchestration (confirmation
round-trips, cancellation watching, speech interruption) lives in
agent/voice_session.py, where it can be unit-tested without a real
microphone or AppKit.

Run directly with `python3 -m ui.menu_bar` from the project root (with the
venv active), or via the CampusPilot.app icon once that's pointed here.
"""
import atexit
import concurrent.futures
import os
import queue
import threading
import time
from datetime import datetime

import rumps

from agent import voice_session, voice_state
from agent.audit import recent_actions_text
from agent.computer_use_status import is_active as computer_use_active
from agent.executor import execute_task
from agent.memory_agent import recall
from agent.scheduled_tasks import list_tasks, mark_run
from agent.voice_state import VoiceState
from config.settings import settings
from voice.listen import is_exit_phrase, listen_for_followup, wait_for_command

USER_NAME = settings.user_name

SCHEDULER_POLL_SECONDS = settings.scheduler_poll_seconds

# One menu-bar title emoji per VoiceState -- the only UI change Phase 6
# makes here, everything else about the menu-bar shell is untouched.
# voice_state.get_status() already composes voice-local phases (LISTENING/
# TRANSCRIBING/SPEAKING/etc) with the real cross-process task status
# (THINKING/EXECUTING/WAITING_FOR_CONFIRMATION/etc, from agent.jarvis_state
# -- Phase 5), so this is the one place that needs to know about either.
_VOICE_STATUS_EMOJI = {
    VoiceState.IDLE: "🤖",
    VoiceState.LISTENING: "👂",
    VoiceState.TRANSCRIBING: "✍️",
    VoiceState.THINKING: "🤔",
    VoiceState.PLANNING: "🧭",
    VoiceState.EXECUTING: "⚙️",
    VoiceState.WAITING_FOR_CONFIRMATION: "⏳",
    VoiceState.SPEAKING: "🗣️",
    VoiceState.CANCELLED: "🛑",
    VoiceState.ERROR: "⚠️",
}

# Single-instance guard (section 17) -- Phase 3 found a real duplicate-
# LaunchAgent bug that started two competing microphone listeners; this is
# a direct, in-process guard against that class of problem (a stale
# LaunchAgent config, or just manually launching the app twice) rather
# than relying solely on the LaunchAgent staying correctly configured.
APP_LOCK_FILE = os.path.expanduser("~/Library/Application Support/CampusPilot/menubar.pid")


def _acquire_single_instance_lock() -> bool:
    if os.path.exists(APP_LOCK_FILE):
        try:
            with open(APP_LOCK_FILE) as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)  # signal 0: raises if that pid doesn't exist, no-op otherwise
            return False  # a real, live instance is already running
        except ProcessLookupError:
            pass  # stale lock file (leftover from a crash) -- safe to take over
        except PermissionError:
            return False  # exists, just owned by someone else -- treat as running
        except ValueError:
            pass  # corrupt lock file -- safe to take over

    os.makedirs(os.path.dirname(APP_LOCK_FILE), exist_ok=True)
    with open(APP_LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))
    return True


def _release_single_instance_lock() -> None:
    if not os.path.exists(APP_LOCK_FILE):
        return
    try:
        with open(APP_LOCK_FILE) as f:
            pid = int(f.read().strip())
        if pid == os.getpid():
            os.remove(APP_LOCK_FILE)
    except (ValueError, OSError):
        pass


class CampusPilotApp(rumps.App):

    def __init__(self):

        super().__init__("🤖", quit_button="Quit CampusPilot")

        self.conversation = []

        self.menu = [
            "Ask CampusPilot",
            None,
            "Recent Notes",
            "Recent Tasks",
            "Recent Actions",
        ]

        # Background thread -> main thread handoff, so AppKit calls
        # (title, notifications) only ever happen via the main-thread timer.
        self.events = queue.Queue()
        self.poll_timer = rumps.Timer(self._drain_events, 0.5)
        self.poll_timer.start()

        self.stop_flag = threading.Event()

        # Bounds how long a single request can block the listening loop --
        # a stuck network call should never be able to make the assistant
        # permanently deaf.
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)

        if settings.voice_enabled:
            self.listener_thread = threading.Thread(
                target=self._voice_loop,
                daemon=True
            )
            self.listener_thread.start()
        else:
            self.listener_thread = None

        # Runs scheduled tasks (from schedule_task) right here in the
        # always-on menu-bar process, so "tell me the weather every
        # morning at 8am" actually fires and is spoken out loud -- not
        # just a silent notification banner from a separate script the
        # user would have to remember to start (agent/scheduler_daemon.py
        # still exists standalone, but this makes that a non-requirement).
        # Don't run scheduler_daemon.py at the same time as this app --
        # see the lifecycle note at the top of that file for why (each
        # scheduled task would fire twice).
        self.scheduler_thread = threading.Thread(
            target=self._scheduler_loop,
            daemon=True
        )

        self.scheduler_thread.start()


    def _speak(self, text):
        """Speaks `text`, listening concurrently for a wake-word
        interruption -- returns the command said right after an
        interruption (the caller should treat it as the user's next
        request), or None if speech finished normally."""
        return voice_session.speak_with_interruption_watch(text)


    def _speak_and_notify(self, request, text):
        self.conversation.append({"role": "user", "content": request})
        self.conversation.append({"role": "assistant", "content": text})
        self.events.put(("response", request, text))
        self._speak(text)


    def _run_and_report(self, request):
        """Runs one request through the existing Jarvis core (via
        agent.voice_session) and speaks the reply -- busy-state protected
        (section 10: a second voice/scheduled request can't start while
        one is already running) and handles the confirmation-conversation
        round-trip (sections 6-7) around it."""

        if voice_session.is_bare_stop_phrase(request) and not voice_state.is_busy():
            self._speak_and_notify(request, "I'm not currently running a task.")
            return

        if not voice_state.try_start():
            self._speak_and_notify(request, "I'm already working on something -- one moment.")
            return

        try:
            next_request = self._run_conversation_turn(request)
        finally:
            voice_state.finish()

        if next_request:
            self._run_and_report(next_request)


    def _run_conversation_turn(self, request):
        """Runs one request through the core, handling the confirmation
        round-trip if one comes up (bounded to 2 exchanges, so a
        persistently ambiguous back-and-forth can't loop forever). Returns
        a new request string if the user's reply interrupted Jarvis
        mid-speech with a fresh command (the caller should run that next),
        else None."""

        pending_request = request

        for _ in range(2):

            self.conversation.append({"role": "user", "content": pending_request})

            try:
                result = self.executor.submit(
                    voice_session.run_request_with_cancellation_watch,
                    pending_request, self.conversation,
                ).result(timeout=300)
            except concurrent.futures.TimeoutError:
                result = voice_session.VoiceRunResult(
                    text="That took too long, so I gave up on it. Try again?"
                )
            except Exception as error:
                result = voice_session.VoiceRunResult(text=f"Something went wrong: {error}")

            self.conversation.append({"role": "assistant", "content": result.text})
            self.events.put(("response", pending_request, result.text))

            interrupted = self._speak(result.text)
            if interrupted:
                return interrupted

            if not result.needs_confirmation:
                return None

            response_text = listen_for_followup(
                self.stop_flag, max_wait_seconds=settings.voice_confirmation_timeout,
            )
            if not response_text:
                self._speak("No response, so I didn't go ahead.")
                return None

            decision = voice_session.classify_confirmation_response(response_text)
            if decision == "negative":
                self._speak("Okay, cancelled.")
                return None

            # affirmative or unclear -- resend through the core so the
            # model either re-attempts the SAME pending tool call (which
            # is the only thing agent.autonomy's ledger will actually let
            # through) or uses its own judgment with full context.
            pending_request = response_text

        self._speak("I'll leave it there for now.")
        return None


    def _scheduler_loop(self):

        while not self.stop_flag.is_set():

            try:
                self._run_due_scheduled_tasks()
            except Exception as error:
                self.events.put(("error", f"[scheduler] {error}"))

            # wait() (not sleep()) so quitting the app doesn't have to sit
            # through a stale poll interval first.
            self.stop_flag.wait(SCHEDULER_POLL_SECONDS)


    def _run_due_scheduled_tasks(self):

        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        current_hm = now.strftime("%H:%M")

        for task in list_tasks():

            if not task.get("enabled", True):
                continue
            if task.get("last_run_date") == today:
                continue
            if task["time_of_day"] != current_hm:
                continue

            if not voice_state.try_start():
                # A voice conversation is active right now -- don't compete
                # for the mic/speaker or the Jarvis core. Not marked run,
                # so this fires on the next poll tick instead of being
                # silently skipped for the day.
                continue

            try:
                result = execute_task(task["prompt"], source="scheduled")
                mark_run(task["id"], today)
                self.events.put(("response", task["prompt"], result))
                self._speak(result)
            finally:
                voice_state.finish()


    @rumps.clicked("Ask CampusPilot")
    def ask(self, _):

        response = rumps.Window(
            title="CampusPilot",
            message=f"What can I help you with, {USER_NAME}?",
            ok="Send",
            cancel="Cancel",
            dimensions=(320, 100)
        ).run()

        if not response.clicked or not response.text.strip():
            return

        self.executor.submit(self._run_and_report, response.text.strip())


    def _voice_loop(self):

        while not self.stop_flag.is_set():

            try:

                request = wait_for_command(self.stop_flag)

                if request is None:
                    return

                if not request:
                    continue

                self._run_and_report(request)

                # Active conversation mode: once woken, keep listening
                # without requiring "Jarvis" again for every follow-up --
                # mirrors app.py's browser passive/active split. Ends on
                # "Jarvis, that's all" (or similar) or ~2 minutes of
                # silence, whichever comes first, then drops back to
                # requiring the wake word.
                while not self.stop_flag.is_set():

                    followup = listen_for_followup(self.stop_flag)

                    if followup is None:
                        break

                    if not followup:
                        continue

                    if is_exit_phrase(followup):
                        break

                    self._run_and_report(followup)

            except Exception as error:

                voice_state.set_status(VoiceState.ERROR)
                self.events.put(("error", str(error)))
                time.sleep(5)

            finally:

                voice_state.reset_to_idle()


    def _drain_events(self, _):

        while not self.events.empty():

            event = self.events.get()

            if event[0] == "response":

                _, request, result = event

                rumps.notification(
                    "CampusPilot",
                    request[:80],
                    result[:250]
                )

            elif event[0] == "error":

                print(f"[voice] {event[1]}")

        # Checked every tick (not just on a queued event) so the icon
        # reflects real mouse/keyboard control, or a status change from a
        # background thread, the moment it happens.
        if computer_use_active():
            self.title = "🖱️"
        else:
            self.title = _VOICE_STATUS_EMOJI.get(voice_state.get_status(), "🤖")


    @rumps.clicked("Recent Notes")
    def show_notes(self, _):

        rumps.alert("Recent Notes", recall("notes"))


    @rumps.clicked("Recent Tasks")
    def show_tasks(self, _):

        tasks = list_tasks()

        if not tasks:
            rumps.alert("Recent Tasks", "Nothing scheduled yet.")
            return

        text = "\n".join(
            f"- {task['prompt']} ({task['time_of_day']})" for task in tasks
        )

        rumps.alert("Recent Tasks", text)


    @rumps.clicked("Recent Actions")
    def show_actions(self, _):

        rumps.alert("Recent Actions", recent_actions_text(10))


if __name__ == "__main__":

    if not _acquire_single_instance_lock():
        print("CampusPilot is already running -- not starting a second microphone listener.")
        raise SystemExit(1)

    atexit.register(_release_single_instance_lock)

    CampusPilotApp().run()
