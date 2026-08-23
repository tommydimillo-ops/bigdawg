"""The one authoritative test-safety bootstrap for this project's suite.

Installed exactly once by `tests/__init__.py`, before any individual
`tests/test_*.py` module is imported -- see that file's own docstring for
exactly which invocation styles guarantee that ordering (short version:
any invocation that imports `tests` as a real package, which requires
`-t .`/a dotted `tests.test_x` path; a bare `python tests/test_x.py` does
not and is not a safety-guaranteed invocation).

WHY THIS EXISTS (Phase 9 reliability audit, 2026-08-23): the audit proved
that the canonical full-suite command actually written down everywhere in
this project's history never executed `tests/__init__.py` at all, and
that the suite -- run exactly that way -- silently wrote into six real
production files, including a real macOS Keychain entry. The audit's
full findings and reasoning live in that report; this module is the
architecture it recommended, not a redesign of anything else.

WHAT THIS MODULE OWNS:
- one disposable temp directory ("the run root"), created fresh per test
  process, used as the redirect target for every production persistent
  store this project has (see PATCHES below for the exact list) --
  never any location under the real
  `~/Library/Application Support/CampusPilot`.
- an external-network firewall at the stdlib `socket` layer: loopback
  (127.0.0.0/8, ::1, "localhost") is allowed, anything else raises
  `ExternalNetworkBlocked` before a real connection/DNS lookup happens.
- a secondary, higher-level `httpx` transport tripwire, purely for a
  clearer error message when a provider SDK call is the thing that got
  misrouted -- the socket firewall above is the actual enforcement
  boundary and would catch the same call regardless.
- a Playwright/computer-use tripwire, since real browser network traffic
  runs inside a separate Chrome process and never touches this process's
  `socket` module at all -- the firewall above cannot see it, so it needs
  its own guard at the one real entry point (`tools.browser.sync_playwright`)
  and at `tools.computer_use.pyautogui`.
- a Keychain service-name seam (`tools.credential_store.KEYCHAIN_SERVICE`),
  redirected to a test-only value so an un-mocked test path can never
  reach the real, live `"CampusPilot"` Keychain service by accident.

WHAT THIS MODULE DELIBERATELY DOES NOT DO:
- remove or replace any existing per-file `setUp`/`tearDown` redirect --
  those remain real, working defense-in-depth, not redundant dead code
  (nesting a second redirect on top of one already in effect is the same
  safe pattern this project's USAGE_FILE handling already established).
- weaken or change any production code path's actual behavior -- every
  redirect here only ever reassigns a module-level constant that
  production code already reads dynamically at call time (verified per
  constant, not assumed -- see the audit report for which ones are
  derived from another constant and needed independent redirection).
- touch the real macOS Keychain, a real user's Obsidian vault, a real
  browser, real mouse/keyboard control, or any real external host --
  ever, from this bootstrap.
- decide network policy for PRODUCTION Jarvis. This module is only ever
  imported by `tests/__init__.py`; no production import path reaches it.
"""
import atexit
import os
import shutil
import socket
import tempfile
import threading

TEST_SAFETY_INSTALLED = False

_lock = threading.Lock()
_run_root = None
_real_paths = {}  # constant "module.NAME" -> original production value, for meta-tests
_network_guard_installed = False
_httpx_guard_installed = False
_browser_guard_installed = False


class ExternalNetworkBlocked(Exception):
    """Raised instead of letting test code reach a real, non-loopback
    network destination. Bounded diagnostic only -- destination
    host/port, never any request/response payload or secret."""

    def __init__(self, host, port=None):
        self.host = host
        self.port = port
        detail = f"{host}:{port}" if port is not None else str(host)
        super().__init__(f"external network blocked in tests: {detail}")


class RealBrowserBlocked(Exception):
    """Raised instead of letting a test launch a real, visible browser or
    perform real mouse/keyboard/screenshot control."""


def run_root() -> str:
    """The shared, per-process temp directory every redirect below points
    into. Raises if safety hasn't been installed yet -- callers (tests,
    not production code) should never see an uninitialized value."""
    if _run_root is None:
        raise RuntimeError("tests._safety.install_test_safety() has not run yet")
    return _run_root


def real_production_value(dotted_name: str):
    """Test-only accessor for what a redirected constant's real,
    pre-patch production value was -- e.g. real_production_value(
    "database.memory.MEMORY_FILE"). Used by meta-tests to prove the real
    path was never touched, without hardcoding it a second time."""
    return _real_paths[dotted_name]


# --- Persistent-store redirection -------------------------------------

def _set(module, attr, value, dotted_name):
    _real_paths[dotted_name] = getattr(module, attr)
    setattr(module, attr, value)


def _install_store_redirects(root: str) -> None:
    """Every production path constant the audit's full inventory found --
    not just the ones a prior pass happened to catch. Each assignment
    here was checked against the real function that actually performs
    the write/read: every one of them resolves the module-level name
    dynamically at call time (a plain global lookup inside the function
    body), never as a default-parameter value baked in at definition
    time -- so reassigning the module attribute here, before any of
    these functions is ever called, is sufficient. Three constants
    (agent.audit.LOG_FILE, tools.credential_store.LOGINS_FILE,
    tools.sandbox_python.PROFILE_PATH) are derived from a sibling
    constant at import time and are therefore redirected independently,
    not left to "follow" their sibling's redirect.

    Two real production bugs of the exact "captured at definition time"
    class this module's docstring warns about were found and fixed
    (production code, not this file) while building this redirect list:
    tools.sandbox_python's Seatbelt profile string used to be a
    module-level f-string baking in SANDBOX_DIR's import-time value --
    now built fresh inside _ensure_profile() from the current SANDBOX_DIR
    each call. agent.history_store's six public functions and
    agent.personal_context's save_catalog/load_catalog used to default
    their path parameter to the constant itself (`db_path: str =
    HISTORY_DB`), binding that default at module-import time exactly
    like the docstring above says none of these do -- now default to
    None and read the module constant dynamically in the function body.
    Every existing caller already passed its path explicitly (so this
    was previously a silent, untested trap for exactly this kind of
    default-relying test/meta-test, not a live bug in production or the
    existing suite), but it meant a redirect of HISTORY_DB/CATALOG_FILE
    alone did not actually protect a caller that omitted the argument."""
    import agent.audit as audit
    import agent.browser_lock as browser_lock
    import agent.cancellation as cancellation
    import agent.conversation_store as conversation_store
    import agent.execution_history as execution_history
    import agent.history_store as history_store
    import agent.jarvis_state as jarvis_state
    import agent.personal_context as personal_context
    import agent.quiet_mode as quiet_mode
    import agent.scheduled_tasks as scheduled_tasks
    import agent.scheduler_lock as scheduler_lock
    import agent.skills.loader as skills_loader
    import agent.tts_control as tts_control
    import agent.usage as usage
    import database.memory as database_memory
    import tools.computer_use as computer_use
    import tools.browser as browser
    import tools.credential_store as credential_store
    import tools.sandbox_python as sandbox_python

    _set(tts_control, "TTS_PID_FILE", os.path.join(root, "tts.pid"), "agent.tts_control.TTS_PID_FILE")
    _set(cancellation, "CANCEL_DIR", os.path.join(root, "cancellation_requests"), "agent.cancellation.CANCEL_DIR")
    _set(scheduled_tasks, "TASKS_FILE", os.path.join(root, "scheduled_tasks.json"), "agent.scheduled_tasks.TASKS_FILE")
    _set(audit, "LOG_DIR", root, "agent.audit.LOG_DIR")
    _set(audit, "LOG_FILE", os.path.join(root, "audit.log"), "agent.audit.LOG_FILE")
    _set(conversation_store, "CONVERSATION_FILE", os.path.join(root, "conversation.json"), "agent.conversation_store.CONVERSATION_FILE")
    _set(quiet_mode, "QUIET_MODE_FILE", os.path.join(root, "quiet_mode.json"), "agent.quiet_mode.QUIET_MODE_FILE")
    _set(browser_lock, "BROWSER_LOCK_FILE", os.path.join(root, "chrome-profile.lock"), "agent.browser_lock.BROWSER_LOCK_FILE")
    _set(history_store, "HISTORY_DB", os.path.join(root, "history.db"), "agent.history_store.HISTORY_DB")
    _set(jarvis_state, "STATE_FILE", os.path.join(root, "jarvis_state.json"), "agent.jarvis_state.STATE_FILE")
    _set(personal_context, "CATALOG_FILE", os.path.join(root, "personal_context.json"), "agent.personal_context.CATALOG_FILE")
    _set(execution_history, "HISTORY_FILE", os.path.join(root, "execution_history.json"), "agent.execution_history.HISTORY_FILE")
    _set(scheduler_lock, "SCHEDULER_LOCK_FILE", os.path.join(root, "scheduler.lock"), "agent.scheduler_lock.SCHEDULER_LOCK_FILE")
    _set(usage, "USAGE_FILE", os.path.join(root, "usage_history.json"), "agent.usage.USAGE_FILE")
    _set(database_memory, "MEMORY_FILE", os.path.join(root, "memory.json"), "database.memory.MEMORY_FILE")
    _set(credential_store, "CONFIG_DIR", root, "tools.credential_store.CONFIG_DIR")
    _set(credential_store, "LOGINS_FILE", os.path.join(root, "logins.json"), "tools.credential_store.LOGINS_FILE")
    # Keychain service-name seam: production default is untouched
    # ("CampusPilot", the real live service) -- only this test process's
    # in-memory copy of the module attribute changes, so any test path
    # that reaches save_login/get_login/delete_login without its own
    # explicit keyring mock still lands on a distinct, harmless service
    # name rather than the real one. Not a substitute for mocking
    # `keyring` itself where a test's whole point is exercising this
    # logic -- see tests/test_safety.py's TestConfirmLoginGate.
    _set(credential_store, "KEYCHAIN_SERVICE", "CampusPilot-TEST", "tools.credential_store.KEYCHAIN_SERVICE")
    _set(computer_use, "SCREENSHOT_DIR", os.path.join(root, "computer_use_screenshots"), "tools.computer_use.SCREENSHOT_DIR")
    _set(browser, "PROFILE_DIR", os.path.join(root, "chrome-profile"), "tools.browser.PROFILE_DIR")
    _set(sandbox_python, "SANDBOX_DIR", os.path.join(root, "sandbox"), "tools.sandbox_python.SANDBOX_DIR")
    _set(sandbox_python, "PROFILE_PATH", os.path.join(root, "sandbox.sb"), "tools.sandbox_python.PROFILE_PATH")

    # Skills: the repo's own tracked skills/ directory is reviewed
    # content, safe to leave as-is; the second entry is the real user's
    # Application-Support skills directory, explicitly untrusted input
    # per this project's own security model -- redirected to an empty
    # temp stand-in so canonical tests never ingest a real user's actual
    # skill files.
    _real_paths["agent.skills.loader.DEFAULT_SKILLS_DIRS"] = skills_loader.DEFAULT_SKILLS_DIRS
    repo_skills_dir = skills_loader.DEFAULT_SKILLS_DIRS[0]
    test_user_skills_dir = os.path.join(root, "skills")
    os.makedirs(test_user_skills_dir, exist_ok=True)
    skills_loader.DEFAULT_SKILLS_DIRS = (repo_skills_dir, test_user_skills_dir)

    # Obsidian: settings.obsidian_vault_path is a plain config field, not
    # a module-level path constant like the others -- redirected here so
    # a developer's real OBSIDIAN_VAULT_PATH env var (if ever set) can
    # never leak into the canonical suite, matching the audit's specific
    # finding on this point.
    from config.settings import settings
    _real_paths["config.settings.settings.obsidian_vault_path"] = settings.obsidian_vault_path
    object.__setattr__(settings, "obsidian_vault_path", None)


# --- External-network firewall (stdlib socket layer) --------------------

_LOOPBACK_LITERALS = {"127.0.0.1", "::1", "localhost", ""}


def _is_loopback_host(host) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        try:
            host = host.decode("ascii", "strict")
        except Exception:
            return False
    if not isinstance(host, str):
        return False
    if host in _LOOPBACK_LITERALS:
        return True
    return host.startswith("127.")


def _install_network_guard() -> None:
    """Blocks every non-loopback destination at the lowest common stdlib
    boundary every higher-level library (httpx/httpcore, websockets,
    urllib, a raw socket) ultimately goes through in this process.
    Deliberately does NOT resolve DNS to decide whether to allow a
    hostname -- an unrecognized hostname is blocked before any resolver
    call, exactly like an IP literal. AF_UNIX sockets (local IPC, not
    network) are always allowed and never inspected. This is a TEST-ONLY
    patch on the stdlib `socket` module objects themselves -- never
    installed by, or reachable from, any production import path."""
    global _network_guard_installed
    if _network_guard_installed:
        return
    _network_guard_installed = True

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_sendto = socket.socket.sendto
    real_create_connection = socket.create_connection
    real_getaddrinfo = socket.getaddrinfo

    def _address_host(address):
        if isinstance(address, tuple) and len(address) >= 1:
            return address[0]
        if isinstance(address, str):
            return None  # AF_UNIX path, not a network host
        return address

    def _guard(sock_self, address):
        try:
            if sock_self.family == socket.AF_UNIX:
                return
        except (AttributeError, OSError):
            pass
        host = _address_host(address)
        if host is not None and not _is_loopback_host(host):
            port = address[1] if isinstance(address, tuple) and len(address) > 1 else None
            raise ExternalNetworkBlocked(host, port)

    def guarded_connect(self, address, *a, **kw):
        _guard(self, address)
        return real_connect(self, address, *a, **kw)

    def guarded_connect_ex(self, address, *a, **kw):
        _guard(self, address)
        return real_connect_ex(self, address, *a, **kw)

    def guarded_sendto(self, data, *rest, **kw):
        # socket.sendto(data, address) or socket.sendto(data, flags, address)
        if rest:
            _guard(self, rest[-1])
        return real_sendto(self, data, *rest, **kw)

    def guarded_create_connection(address, *a, **kw):
        host = address[0] if isinstance(address, tuple) else address
        if not _is_loopback_host(host):
            port = address[1] if isinstance(address, tuple) and len(address) > 1 else None
            raise ExternalNetworkBlocked(host, port)
        return real_create_connection(address, *a, **kw)

    def guarded_getaddrinfo(host, *a, **kw):
        if not _is_loopback_host(host):
            raise ExternalNetworkBlocked(host)
        return real_getaddrinfo(host, *a, **kw)

    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    socket.socket.sendto = guarded_sendto
    socket.create_connection = guarded_create_connection
    socket.getaddrinfo = guarded_getaddrinfo


def _install_httpx_guard() -> None:
    """Secondary, higher-level tripwire purely for a clearer diagnostic:
    if provider code reaches httpx's own transport for a non-loopback
    request, fail with the URL right there in the message, rather than a
    generic socket-level error several stack frames deeper. The socket
    guard above is the actual, universal enforcement boundary -- this
    only improves the error a developer sees; it is not itself required
    for the safety property to hold."""
    global _httpx_guard_installed
    if _httpx_guard_installed:
        return
    _httpx_guard_installed = True

    try:
        import httpx
    except ImportError:
        return

    real_handle_request = httpx.HTTPTransport.handle_request

    def guarded_handle_request(self, request):
        host = request.url.host
        if not _is_loopback_host(host):
            raise ExternalNetworkBlocked(host, request.url.port)
        return real_handle_request(self, request)

    httpx.HTTPTransport.handle_request = guarded_handle_request


def _install_browser_guard() -> None:
    """Real browser/computer-control traffic never touches this
    process's `socket` module (Chrome is a separate OS process with its
    own network stack; pyautogui drives real mouse/keyboard/screenshot
    APIs, not a network call at all) -- the guards above cannot see
    either, so each gets its own tripwire at the one real entry point.
    Composes safely with this project's existing per-test
    `@patch("tools.browser.sync_playwright")`-style mocks: mock.patch
    always saves and restores whatever was here before, so a test's own
    mock temporarily overrides this default and this default resumes
    afterward -- never a conflict, matching how nested redirection
    already works for every store above."""
    global _browser_guard_installed
    if _browser_guard_installed:
        return
    _browser_guard_installed = True

    import tools.browser as browser

    def _blocked_sync_playwright(*a, **kw):
        raise RealBrowserBlocked(
            "a test tried to launch a real Playwright/Chrome browser without mocking "
            "tools.browser.sync_playwright first"
        )

    browser.sync_playwright = _blocked_sync_playwright

    import tools.computer_use as computer_use

    class _PoisonedPyautogui:
        """Every attribute access raises -- covers .click/.write/.press/
        .hotkey/.screenshot/.size/.PAUSE and anything else, so a new
        pyautogui call added later fails closed by default instead of
        silently working for real."""

        def __getattr__(self, name):
            raise RealBrowserBlocked(
                f"a test tried to call pyautogui.{name}(...) for real without mocking "
                "tools.computer_use.pyautogui first"
            )

    computer_use.pyautogui = _PoisonedPyautogui()


# --- Bootstrap ------------------------------------------------------------

def install_test_safety() -> None:
    """The single entry point `tests/__init__.py` calls. Idempotent,
    thread-safe, deterministic: calling this more than once (e.g. if
    some future test module does `from tests import _safety;
    _safety.install_test_safety()` defensively) is a safe no-op after
    the first call. Never called from any production import path."""
    global TEST_SAFETY_INSTALLED, _run_root
    with _lock:
        if TEST_SAFETY_INSTALLED:
            return

        # Prefix capitalization matches the real "CampusPilot" branding
        # deliberately -- agent/*.py's own path constants and at least
        # one existing test (tests/test_browser.py's
        # test_profile_dir_is_not_a_real_chrome_profile_path) assert that
        # a real, non-test PROFILE_DIR-style constant contains the
        # literal substring "CampusPilot"; keeping it here too means a
        # redirected value still reads as "this project's own path," not
        # a generic temp directory, and keeps that pre-existing test's
        # assertion meaningful rather than needing to change its intent.
        #
        # os.path.realpath is load-bearing, not cosmetic: macOS's default
        # temp root is /var/folders/..., itself a symlink to
        # /private/var/folders/.... sandbox-exec's Seatbelt profile
        # resolves subpath rules against the canonical path, so a profile
        # written with the symlinked form denies writes to a cwd the
        # kernel sees as /private/var/folders/... -- proven empirically
        # (a real in-sandbox write, which must succeed, was spuriously
        # denied before this fix). Resolving once here means every store
        # redirected below inherits the canonical form.
        _run_root = os.path.realpath(tempfile.mkdtemp(prefix="CampusPilot-test-run-"))
        real_app_support = os.path.expanduser("~/Library/Application Support/CampusPilot")
        if os.path.realpath(_run_root) == os.path.realpath(real_app_support):
            # Structurally impossible in practice (mkdtemp always creates
            # a fresh directory under the system temp root), but this is
            # exactly the invariant a meta-test should never have to take
            # on faith -- fail loudly rather than silently proceeding.
            raise RuntimeError("test run root resolved to the real production Application Support directory")

        _install_store_redirects(_run_root)
        _install_network_guard()
        _install_httpx_guard()
        _install_browser_guard()

        def _cleanup():
            shutil.rmtree(_run_root, ignore_errors=True)

        atexit.register(_cleanup)

        TEST_SAFETY_INSTALLED = True
