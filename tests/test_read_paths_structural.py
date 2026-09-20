"""Plan-b8 -- the structural regression guard for the READ side, the same
shape as tests/test_gating_structural.py does for coworker side effects.

The finding it exists for: every model-reachable file reader used to have
to remember, on its own, to keep secrets out of the prompt (and none did --
`read_document`'s extension whitelist kept `.env` out only by accident).
Plan-b8 routed them all through agent/secret_paths.py. This test makes that
routing something a future change cannot quietly undo or bypass:

  1. It re-derives, from real source on every run (ast, not a list someone
     maintains by hand), every function in the codebase that reads file
     contents -- `open()` in a read-capable mode, `os.open`, `read_text`/
     `read_bytes`, `PdfReader`, and subprocess calls to content-reading
     commands such as `cat`.
  2. DEFAULT-DENY: each such function must either call the chokepoint
     (`refuse_secret_read` / `secret_path_reason`) or appear in
     ACCEPTED_INTERNAL_READERS below with a written reason. A new reader
     added tomorrow, in any file, fails this test until someone either
     guards it or writes down why it does not need to be.
  3. It fails on a STALE accepted entry too (a function that no longer
     reads, or has since been guarded), so the table cannot rot into
     something nobody trusts.

What this cannot see, stated plainly rather than implied away: CODE
EXECUTION. `run_python` (tools/sandbox_python.py) and the test-suite runners
execute model-written code with real read access, so that code can
`open('.env')` without ever touching a reader this file checks -- the
sandbox profile is `(allow default)` and the subprocess inherits the whole
environment. That is a genuinely different problem (it needs a sandbox
read-deny and a scrubbed environment, not a path check), it was found by
plan-b8's enumeration, and it is NOT closed. It is recorded below in
ACKNOWLEDGED_UNGUARDED_CODE_EXECUTION so it is tracked in code rather than
lost in a report, and any NEW function that executes code must be added to
that table or to FIXED_WORKERS -- a decision, not an accident.

The scan also does not cover `sqlite3.connect` (the app's own database at
a fixed path), `mmap`, `ctypes`, or screen capture (`computer_see`).

Run with: python -m unittest tests.test_read_paths_structural -v
"""
import ast
import os
import unittest
from typing import Dict, List, Set, Tuple

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Everything under the repo is scanned EXCEPT these -- so a new top-level
# package is covered automatically rather than silently skipped.
_EXCLUDE_DIRS = frozenset({
    "tests", ".venv", "venv", "build", "dist", "CampusPilotAgent.app", "JarvisVault",
    "graphify-out", ".relay", ".git", "__pycache__", "logs", "docs", "node_modules", "skills",
})

_GUARD_CALL_NAMES = frozenset({"refuse_secret_read", "secret_path_reason"})

# Bases whose `.open(...)` is a file-content open (as opposed to
# webbrowser.open, Image.open on a screenshot object, sock.open, ...).
_FILE_LIB_BASES = frozenset({"io", "codecs", "os", "gzip", "bz2", "lzma", "zipfile", "tarfile"})

# First element of a subprocess argv that reads file content to stdout.
_CONTENT_READING_COMMANDS = frozenset({
    "cat", "head", "tail", "sed", "awk", "strings", "less", "more", "xxd", "hexdump", "od",
    "base64", "grep", "egrep", "fgrep", "rg", "plutil", "defaults", "security", "textutil",
})

Key = Tuple[str, str]  # (repo-relative file, dotted function qualname)

_STORE = "a fixed module-level path to this app's own state file; never caller- or model-supplied"
_LOCK = "an fcntl lock file; its content is never read into anything"
_OWN_OUTPUT = "reads a file this same process just wrote itself (a screenshot / a recording)"

# The full explicit table: every function that reads a file and does NOT go
# through the chokepoint, and why that is acceptable. Each entry is a real
# decision, written down. Built from the plan-b8 enumeration (39 read-capable
# functions found; the 4 model-reachable ones -- _read_file, read_document,
# read_note, search_notes -- are guarded, not listed here).
ACCEPTED_INTERNAL_READERS: Dict[Key, str] = {
    # -- state stores: fixed paths ---------------------------------------
    ("agent/alexa_bridge.py", "load_last_result"): _STORE,
    ("agent/audit.py", "recent_actions"): _STORE,
    ("agent/code_graph.py", "CodeGraphReader._load_graph"): _STORE + " (the generated graph.json)",
    ("agent/conversation_store.py", "_load_unlocked"): _STORE,
    ("agent/execution_history.py", "_load_raw"): _STORE,
    ("agent/jarvis_state.py", "get_state"): _STORE,
    ("agent/memory/access_log.py", "_load_raw"): _STORE,
    ("agent/observability.py", "events_since"): _STORE + " (the menu-bar log; log_path is a test seam)",
    ("agent/personal_context.py", "load_catalog"): _STORE + " (path is a test seam, defaulting to the catalog)",
    ("agent/quiet_mode.py", "_read_state"): _STORE,
    ("agent/scheduled_tasks.py", "_load"): _STORE,
    ("agent/telegram_bridge.py", "load_offset"): _STORE,
    ("agent/tts_control.py", "_tracked_pid_and_command"): _STORE + " (a pid file)",
    ("agent/tts_control.py", "untrack_pid"): _STORE + " (a pid file)",
    ("agent/usage.py", "_load_raw"): _STORE,
    ("database/memory.py", "get_memory"): _STORE,
    ("tools/credential_store.py", "_load_metadata"):
        _STORE + ", holding login METADATA only -- passwords live in the macOS Keychain, not here",
    ("ui/menu_bar.py", "_acquire_single_instance_lock"): _STORE + " (the app's own lock/pid file)",
    ("ui/menu_bar.py", "_release_single_instance_lock"): _STORE + " (the app's own lock/pid file)",
    # -- lock files ------------------------------------------------------
    ("agent/browser_lock.py", "try_acquire"): _LOCK,
    ("agent/coding_checkpoint.py", "_restore_lock"): _LOCK,
    ("agent/conversation_store.py", "_locked"): _LOCK,
    ("agent/execution_history.py", "_persist"): _LOCK,
    ("agent/memory/access_log.py", "record_accesses"): _LOCK,
    ("agent/scheduled_tasks.py", "_locked"): _LOCK,
    ("agent/scheduler_lock.py", "try_acquire"): _LOCK,
    ("agent/telegram_lock.py", "acquire_or_exit"): _LOCK,
    ("agent/usage.py", "_persist"): _LOCK,
    ("agent/history_store.py", "_create_file_with_owner_only_permissions"):
        "os.open with O_CREAT|O_EXCL to CREATE the database file with 0600 permissions; reads nothing",
    # -- a file this process just produced -------------------------------
    ("tools/computer_use.py", "computer_locate"): _OWN_OUTPUT,
    ("voice/listen.py", "transcribe"): _OWN_OUTPUT,
    # -- takes a path but cannot leak it ---------------------------------
    ("agent/coding_checkpoint.py", "restore_paths"):
        "reads a file's current content only to compare it with the checkpoint's; the content is "
        "never returned, logged, or shown to the model",
    ("agent/obsidian_vault.py", "write_note"):
        "reads the existing note only to APPEND to it and returns only a success flag; the path is "
        "confined to the vault and forced to .md",
    ("documents/pdf_reader.py", "read_pdf"):
        "its sole caller is documents.reader.read_document, which runs the chokepoint first -- "
        "asserted by test_read_pdf_has_exactly_one_caller_and_it_is_guarded",
    ("tools/vision.py", "analyze_image"):
        "sends an IMAGE to the vision model; every caller passes a screenshot this process just "
        "captured, never a model-supplied path",
}

# Model-reachable readers that MUST call the chokepoint. If any of these
# stops calling it, it falls out of the guarded set and this test names it.
EXPECTED_GUARDED: Set[Key] = {
    ("agent/agents/coding.py", "_read_file"),
    ("documents/reader.py", "read_document"),
    ("agent/obsidian_vault.py", "read_note"),
    ("agent/obsidian_vault.py", "search_notes"),
}

# Functions that EXECUTE code which can read files, and are NOT closed by
# the chokepoint. Tracked openly -- see the module docstring. When one of
# these is fixed (sandbox read-deny + scrubbed environment), remove it here.
ACKNOWLEDGED_UNGUARDED_CODE_EXECUTION: Dict[Key, str] = {
    ("tools/sandbox_python.py", "run_python"):
        "registered main-loop tool; runs model-written Python under a Seatbelt profile that is "
        "(allow default) -- reads are NOT restricted, and the subprocess inherits the whole "
        "environment (where load_dotenv puts the real keys). Demonstrated in plan-b8 with a FAKE "
        ".env. A narrow (deny file-read* (regex ...)) profile was verified to work in a throwaway "
        "probe; not applied, because it changes this tool's semantics",
    ("agent/agents/coding.py", "_run_test_suite"):
        "CodingAgent's run_tests: runs the suite, which includes test files the agent itself wrote; "
        "the last lines of output go back to the model",
    ("agent/agents/coding.py", "_collected_test_count"):
        "runs a collection pass over a test file the agent wrote",
    ("agent/agents/qa.py", "_run_test_suite"):
        "QAAgent's suite run; would execute any test file CodingAgent wrote",
}

# Code executors that only ever run a FIXED module of this project, never
# model-supplied code.
FIXED_WORKERS: Dict[Key, str] = {
    ("agent/canonical_suite.py", "canonical_suite_command"): "builds the fixed `python -m unittest discover` argv",
    ("agent/agents/manager.py", "execute_agent"): "spawns the fixed `python -m agent.agents.worker` module",
    ("voice/local_transcribe.py", "transcribe_local"): "spawns the fixed transcription worker module",
}


# ---------------------------------------------------------------------------
# The scanner
# ---------------------------------------------------------------------------

def _is_read_mode(call: ast.Call) -> bool:
    mode = call.args[1] if len(call.args) > 1 else None
    for keyword in call.keywords:
        if keyword.arg == "mode":
            mode = keyword.value
    if mode is None:
        return True  # open(path) defaults to text read
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        text = mode.value
        return "r" in text or "+" in text or not any(char in text for char in "wax")
    return True  # a non-constant mode: assume it can read


def _read_primitive(node: ast.AST):
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Name):
        if func.id == "open":
            return "open" if _is_read_mode(node) else None
        if func.id == "PdfReader":
            return "PdfReader"
    if isinstance(func, ast.Attribute):
        if func.attr in ("read_text", "read_bytes"):
            return func.attr
        if func.attr == "open" and isinstance(func.value, ast.Name) and func.value.id in _FILE_LIB_BASES:
            if func.value.id == "os":
                return "os.open"
            return f"{func.value.id}.open" if _is_read_mode(node) else None
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    if name in {"run", "Popen", "check_output", "call", "check_call"} and node.args:
        first = node.args[0]
        if (
            isinstance(first, (ast.List, ast.Tuple)) and first.elts
            and isinstance(first.elts[0], ast.Constant) and first.elts[0].value in _CONTENT_READING_COMMANDS
        ):
            return f"subprocess:{first.elts[0].value}"
    return None


class _Scanner(ast.NodeVisitor):
    def __init__(self):
        self.stack: List[str] = []
        self.reads: List[Tuple[str, str, int]] = []   # (qualname, primitive, line)
        self.guarded: Set[str] = set()
        self.code_exec: Set[str] = set()
        self.calls: List[Tuple[str, str, int]] = []    # (qualname, callee name, line)

    def _qualname(self) -> str:
        return ".".join(self.stack) or "<module>"

    def _function(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = _function
    visit_AsyncFunctionDef = _function

    def visit_ClassDef(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def visit_Call(self, node):
        qualname = self._qualname()
        primitive = _read_primitive(node)
        if primitive:
            self.reads.append((qualname, primitive, node.lineno))
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name in _GUARD_CALL_NAMES:
            self.guarded.add(qualname)
        if name == "canonical_suite_command":
            self.code_exec.add(qualname)
        if name:
            self.calls.append((qualname, name, node.lineno))
        self.generic_visit(node)

    def visit_Attribute(self, node):
        if node.attr == "executable" and isinstance(node.value, ast.Name) and node.value.id == "sys":
            self.code_exec.add(self._qualname())
        self.generic_visit(node)

    def visit_Constant(self, node):
        if node.value == "sandbox-exec":
            self.code_exec.add(self._qualname())


def scan_source(source: str) -> _Scanner:
    scanner = _Scanner()
    scanner.visit(ast.parse(source))
    return scanner


def _scan_repo() -> Dict[str, _Scanner]:
    scanned: Dict[str, _Scanner] = {}
    for directory, subdirs, files in os.walk(_PROJECT_ROOT):
        subdirs[:] = [d for d in subdirs if d not in _EXCLUDE_DIRS and not d.startswith(".")]
        for filename in files:
            if not filename.endswith(".py"):
                continue
            path = os.path.join(directory, filename)
            rel = os.path.relpath(path, _PROJECT_ROOT).replace(os.sep, "/")
            with open(path, encoding="utf-8") as handle:
                try:
                    scanned[rel] = scan_source(handle.read())
                except SyntaxError:
                    continue
    return scanned


def _read_functions(scanned: Dict[str, _Scanner]) -> Dict[Key, List[Tuple[str, int]]]:
    found: Dict[Key, List[Tuple[str, int]]] = {}
    for rel, scanner in scanned.items():
        for qualname, primitive, line in scanner.reads:
            found.setdefault((rel, qualname), []).append((primitive, line))
    return found


def _guarded_keys(scanned: Dict[str, _Scanner]) -> Set[Key]:
    return {(rel, qualname) for rel, scanner in scanned.items() for qualname in scanner.guarded}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestEveryFileReadingFunctionIsGuardedOrAccepted(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.scanned = _scan_repo()
        cls.reads = _read_functions(cls.scanned)
        cls.guarded = _guarded_keys(cls.scanned)

    def test_the_scan_actually_covers_the_codebase(self):
        # Guards against passing vacuously (a wrong root, a bad exclude).
        self.assertGreater(len(self.scanned), 80)
        self.assertGreater(len(self.reads), 25)

    def test_no_file_reading_function_is_ungated_and_unaccepted(self):
        unguarded = {key for key in self.reads if key not in self.guarded}
        unexpected = sorted(unguarded - set(ACCEPTED_INTERNAL_READERS))
        detail = "\n".join(
            f"  {rel}::{qualname}  {self.reads[(rel, qualname)]}" for rel, qualname in unexpected
        )
        self.assertFalse(
            unexpected,
            "\n\nThese functions read file contents but neither call agent.secret_paths."
            "refuse_secret_read/secret_path_reason nor appear in ACCEPTED_INTERNAL_READERS:\n"
            f"{detail}\n\nA read path that can be steered by a model must go through the chokepoint "
            "(reads leave the machine irreversibly). If the path is genuinely fixed and internal, add "
            "it to ACCEPTED_INTERNAL_READERS with the reason.",
        )

    def test_no_accepted_entry_is_stale(self):
        unguarded = {key for key in self.reads if key not in self.guarded}
        stale = sorted(set(ACCEPTED_INTERNAL_READERS) - unguarded)
        self.assertFalse(
            stale,
            f"ACCEPTED_INTERNAL_READERS lists functions that no longer read a file, or are now "
            f"guarded, or were renamed/moved -- remove or update them: {stale}",
        )

    def test_every_accepted_entry_states_a_reason(self):
        for key, reason in ACCEPTED_INTERNAL_READERS.items():
            with self.subTest(key=key):
                self.assertGreater(len(reason.strip()), 20)

    def test_the_model_reachable_readers_all_call_the_chokepoint(self):
        for key in sorted(EXPECTED_GUARDED):
            with self.subTest(key=key):
                self.assertIn(key, self.reads, f"{key} no longer reads a file -- update EXPECTED_GUARDED")
                self.assertIn(key, self.guarded, f"{key} reads files but no longer calls the secret-read chokepoint")

    def test_read_pdf_has_exactly_one_caller_and_it_is_guarded(self):
        callers = {
            (rel, qualname)
            for rel, scanner in self.scanned.items()
            for qualname, callee, _ in scanner.calls
            if callee == "read_pdf"
        }
        self.assertEqual(callers, {("documents/reader.py", "read_document")}, callers)
        self.assertIn(("documents/reader.py", "read_document"), self.guarded)


class TestCodeExecutionIsTrackedNotHidden(unittest.TestCase):
    """The chokepoint cannot cover code that runs with the user's own file
    access. This does not claim to fix that -- it makes sure every function
    that executes code is a written-down decision."""

    @classmethod
    def setUpClass(cls):
        cls.scanned = _scan_repo()
        cls.executors = {
            (rel, qualname) for rel, scanner in cls.scanned.items() for qualname in scanner.code_exec
        }

    def test_every_code_executing_function_is_acknowledged_or_a_fixed_worker(self):
        known = set(ACKNOWLEDGED_UNGUARDED_CODE_EXECUTION) | set(FIXED_WORKERS)
        unexpected = sorted(self.executors - known)
        self.assertFalse(
            unexpected,
            f"\n\nThese functions execute code (sys.executable / sandbox-exec / the suite runner) and are "
            f"in neither ACKNOWLEDGED_UNGUARDED_CODE_EXECUTION nor FIXED_WORKERS: {unexpected}\n"
            "Code that runs with the user's file access can read secrets without touching any guarded "
            "reader -- decide which table it belongs in, and why.",
        )

    def test_no_code_execution_entry_is_stale(self):
        stale = sorted((set(ACKNOWLEDGED_UNGUARDED_CODE_EXECUTION) | set(FIXED_WORKERS)) - self.executors)
        self.assertFalse(stale, f"stale code-execution entries -- remove or update: {stale}")

    def test_the_known_open_paths_are_still_recorded(self):
        # If run_python is ever made safe, delete its entry deliberately;
        # this fails first if it is dropped by accident.
        self.assertIn(("tools/sandbox_python.py", "run_python"), ACKNOWLEDGED_UNGUARDED_CODE_EXECUTION)
        self.assertIn(("agent/agents/coding.py", "_run_test_suite"), ACKNOWLEDGED_UNGUARDED_CODE_EXECUTION)


class TestTheScannerItself(unittest.TestCase):
    """So the structural test cannot pass by scanning nothing."""

    def _reads(self, source):
        return [(q, p) for q, p, _ in scan_source(source).reads]

    def test_flags_a_plain_read_and_an_explicit_read_mode(self):
        self.assertEqual(self._reads("def f(p):\n    return open(p).read()\n"), [("f", "open")])
        self.assertEqual(self._reads("def f(p):\n    return open(p, 'rb').read()\n"), [("f", "open")])
        self.assertEqual(self._reads("def f(p):\n    return open(p, mode='r').read()\n"), [("f", "open")])

    def test_ignores_write_only_opens(self):
        for mode in ("w", "wb", "a", "x"):
            with self.subTest(mode=mode):
                self.assertEqual(self._reads(f"def f(p):\n    open(p, '{mode}').write('x')\n"), [])

    def test_read_capable_plus_modes_and_unknown_modes_are_flagged(self):
        self.assertEqual(self._reads("def f(p):\n    open(p, 'a+')\n"), [("f", "open")])
        self.assertEqual(self._reads("def f(p, m):\n    open(p, m)\n"), [("f", "open")])

    def test_flags_the_other_read_primitives(self):
        self.assertEqual(self._reads("def f(p):\n    return Path(p).read_text()\n"), [("f", "read_text")])
        self.assertEqual(self._reads("def f(p):\n    return Path(p).read_bytes()\n"), [("f", "read_bytes")])
        self.assertEqual(self._reads("def f(p):\n    return os.open(p, 0)\n"), [("f", "os.open")])
        self.assertEqual(self._reads("def f(p):\n    return PdfReader(p)\n"), [("f", "PdfReader")])
        self.assertEqual(self._reads("def f(p):\n    return io.open(p)\n"), [("f", "io.open")])

    def test_flags_a_subprocess_that_cats_a_file_but_not_an_unrelated_command(self):
        self.assertEqual(
            self._reads("def f(p):\n    return subprocess.run(['cat', p], capture_output=True)\n"),
            [("f", "subprocess:cat")],
        )
        self.assertEqual(self._reads("def f():\n    return subprocess.run(['pmset', '-g', 'batt'])\n"), [])

    def test_does_not_flag_unrelated_open_methods(self):
        self.assertEqual(self._reads("def f(u):\n    webbrowser.open(u)\n    Image.open(u)\n"), [])

    def test_attributes_a_read_to_the_innermost_function_and_methods_to_their_class(self):
        source = "class C:\n    def m(self, p):\n        def inner():\n            return open(p).read()\n        return inner()\n"
        self.assertEqual(self._reads(source), [("C.m.inner", "open")])

    def test_a_chokepoint_call_marks_only_its_own_function_as_guarded(self):
        scanner = scan_source(
            "def guarded(p):\n    if refuse_secret_read(p, reader='x'):\n        return 1\n    return open(p).read()\n"
            "def other(p):\n    return open(p).read()\n"
        )
        self.assertEqual(scanner.guarded, {"guarded"})
        self.assertEqual({q for q, _, _ in scanner.reads}, {"guarded", "other"})

    def test_detects_code_executors(self):
        self.assertEqual(scan_source("def f():\n    return [sys.executable, '-c', 'x']\n").code_exec, {"f"})
        self.assertEqual(scan_source("def f():\n    return canonical_suite_command()\n").code_exec, {"f"})
        self.assertEqual(scan_source("def f():\n    return ['sandbox-exec', '-f', 'p']\n").code_exec, {"f"})


if __name__ == "__main__":
    unittest.main()
