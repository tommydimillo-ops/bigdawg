"""Native macOS menu-bar presence for CampusPilot -- an always-on Jarvis
that doesn't depend on a browser tab being open. Ported from
CampusPilot_v3/ui/menu_bar.py (the rumps app shell, background-thread/
main-thread event-queue pattern, and native confirm/notification UX), but
wired to *this* project's existing, tested backend --
agent.executor.execute_task -- instead of v3's own agent engine, so it
inherits the same tool set, permission levels, and audit logging as the
Streamlit app rather than a second, separately-secured code path.

Run directly with `python3 -m ui.menu_bar` from the project root (with the
venv active), or via the CampusPilot.app icon once that's pointed here.
"""
import concurrent.futures
import queue
import threading
import time
from datetime import datetime

import rumps

from agent.audit import recent_actions_text
from agent.computer_use_status import is_active as computer_use_active
from agent.executor import execute_task
from agent.memory_agent import recall
from agent.scheduled_tasks import list_tasks, mark_run
from voice.listen import is_exit_phrase, listen_for_followup, wait_for_command
from voice.speak import speak_natural

USER_NAME = "Tommy"

SCHEDULER_POLL_SECONDS = 30


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

        self.listener_thread = threading.Thread(
            target=self._voice_loop,
            daemon=True
        )

        self.listener_thread.start()

        # Runs scheduled tasks (from schedule_task) right here in the
        # always-on menu-bar process, so "tell me the weather every
        # morning at 8am" actually fires and is spoken out loud -- not
        # just a silent notification banner from a separate script the
        # user would have to remember to start (agent/scheduler_daemon.py
        # still exists standalone, but this makes that a non-requirement).
        self.scheduler_thread = threading.Thread(
            target=self._scheduler_loop,
            daemon=True
        )

        self.scheduler_thread.start()


    def _run_and_report(self, request):

        self.events.put(("status", "🤔"))

        self.conversation.append(
            {"role": "user", "content": request}
        )

        future = self.executor.submit(
            execute_task,
            request,
            self.conversation,
            "chat",
        )

        try:
            result = future.result(timeout=300)
        except concurrent.futures.TimeoutError:
            result = "That took too long, so I gave up on it. Try again?"
        except Exception as error:
            result = f"Something went wrong: {error}"

        self.conversation.append(
            {"role": "assistant", "content": result}
        )

        self.events.put(("response", request, result))

        # Speak from here (subprocess call, safe on any thread) rather than
        # in _drain_events. This runs on the same thread that's about to go
        # listen for the next wake word, so speak_natural() blocking until
        # playback actually finishes guarantees the mic isn't recalibrating
        # its ambient-noise threshold while the assistant is still talking
        # -- that was making it deaf to real speech after every response.
        speak_natural(result)
        time.sleep(0.3)


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

            result = execute_task(task["prompt"], source="scheduled")
            mark_run(task["id"], today)

            self.events.put(("response", task["prompt"], result))
            speak_natural(result)


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

                self.events.put(("status", "👂"))

                request = wait_for_command(self.stop_flag)

                if request is None:
                    return

                if not request:
                    self.events.put(("status", "🤖"))
                    continue

                self._run_and_report(request)

                # Active conversation mode: once woken, keep listening
                # without requiring "Jarvis" again for every follow-up --
                # mirrors app.py's browser passive/active split. Ends on
                # "Jarvis, that's all" (or similar) or ~2 minutes of
                # silence, whichever comes first, then drops back to
                # requiring the wake word.
                while not self.stop_flag.is_set():

                    self.events.put(("status", "🟢"))

                    followup = listen_for_followup(self.stop_flag)

                    if followup is None:
                        break

                    if not followup:
                        continue

                    if is_exit_phrase(followup):
                        break

                    self._run_and_report(followup)

            except Exception as error:

                self.events.put(("error", str(error)))
                time.sleep(5)


    def _drain_events(self, _):

        while not self.events.empty():

            event = self.events.get()

            if event[0] == "status":

                self.title = event[1]

            elif event[0] == "response":

                _, request, result = event

                self.title = "🤖"

                rumps.notification(
                    "CampusPilot",
                    request[:80],
                    result[:250]
                )

            elif event[0] == "error":

                self.title = "🤖"

                print(f"[voice] {event[1]}")

        # Checked every tick (not just on a queued event) so the icon
        # reflects real mouse/keyboard control the moment it starts,
        # regardless of whatever other status was showing.
        if computer_use_active():
            self.title = "🖱️"


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

    CampusPilotApp().run()
