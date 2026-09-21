"""The one place this project calls `load_dotenv()`.

Why a shared function: `.env` is where the real API keys live, and three
modules (`config.settings`, `agent.chat`, `tools.manage_secrets`) each used
to call `load_dotenv()` on their own. `run_python` runs model-written code in
a child process that must never see those keys; scrubbing the child's
environment is not enough on its own, because any child that imports one of
those modules would simply re-read `.env` from disk and put the keys straight
back. So the child is started with JARVIS_NO_DOTENV=1, and every caller goes
through here, so honoring that flag cannot be forgotten at a site (plan-b9).

This is one of two independent layers -- the other is the sandbox's read-deny
on `.env` (tools/sandbox_python.py). Neither alone is the fix.
"""
import os

from dotenv import load_dotenv

NO_DOTENV_ENV_VAR = "JARVIS_NO_DOTENV"


def load_project_dotenv() -> bool:
    """Loads `.env` into the environment unless JARVIS_NO_DOTENV is set.
    Returns whether it actually loaded. Idempotent, like load_dotenv()."""
    if os.environ.get(NO_DOTENV_ENV_VAR):
        return False
    load_dotenv()
    return True
