"""Opt-in, real-Keychain integration smoke test for tools/credential_store.py.

Run this directly in a terminal — it is intentionally NOT a `tests/test_*.py`
file and is never discovered or run by `python -m unittest discover -s tests
-t . -v` or by CI. The canonical suite proves credential_store's metadata
logic against a mocked `keyring` boundary (see
`tests/test_safety.py::TestConfirmLoginGate`'s docstring for why: even a
distinctly-named service raised a real macOS Keychain API error,
`(100028, 'Unknown Error')`, in a non-interactive session with no GUI to
answer the access-control prompt). This script exists for the rarer case of
actually wanting to prove the real Keychain seam still works end to end —
e.g. after a `keyring` library upgrade or a macOS version change — run it by
hand, from a normal interactive terminal session, where a Keychain
access-control prompt can actually be answered.

This never touches Jarvis's production Keychain entries. It uses its own
service namespace (`KEYCHAIN_SERVICE_SMOKE_TEST` below), distinct from both
the real production service ("CampusPilot", used by the live app) and the
mocked-keyring unit test's service name ("CampusPilot-TEST", used by
tests/_safety.py — never actually reaches real Keychain since that path
mocks `keyring` entirely). It also redirects credential_store's metadata
file to a throwaway temp directory for the duration of the run, so it never
touches the real logins.json either. Only synthetic, clearly-fake
credentials are used, and the password value is never printed.

Usage:
    python -m tools.keychain_smoke_test
"""

import shutil
import sys
import tempfile

import tools.credential_store as credential_store

KEYCHAIN_SERVICE_SMOKE_TEST = "CampusPilot-Keychain-Smoke-Test"

SITE = "jarvis-keychain-smoke-test"
DOMAIN = "example.invalid"
USERNAME = "smoke-test-user"
PASSWORD = "synthetic-smoke-test-password-not-a-real-credential"


def main():
    print(
        "This will make real calls to the macOS Keychain under a dedicated "
        f"test-only service name ({KEYCHAIN_SERVICE_SMOKE_TEST!r}) -- never "
        "the real Jarvis service. macOS may show a Keychain access prompt; "
        "this must be run from an interactive terminal that can answer it."
    )
    confirm = input("Continue? [y/N] ")
    if confirm.strip().lower() != "y":
        print("Cancelled.")
        return 1

    real_config_dir = credential_store.CONFIG_DIR
    real_logins_file = credential_store.LOGINS_FILE
    real_service = credential_store.KEYCHAIN_SERVICE

    temp_dir = tempfile.mkdtemp(prefix="CampusPilot-keychain-smoke-")
    credential_store.CONFIG_DIR = temp_dir
    credential_store.LOGINS_FILE = f"{temp_dir}/logins.json"
    credential_store.KEYCHAIN_SERVICE = KEYCHAIN_SERVICE_SMOKE_TEST

    try:
        print(f"Saving synthetic login {SITE!r}...")
        credential_store.save_login(SITE, DOMAIN, USERNAME, PASSWORD)

        print("Reading it back...")
        entry = credential_store.get_login(SITE)
        if entry is None:
            print("FAIL: get_login returned None after save_login.")
            return 1
        if entry["password"] != PASSWORD:
            print("FAIL: round-tripped password did not match what was saved.")
            return 1
        print(f"OK: round-trip succeeded (domain={entry['domain']!r}, username={entry['username']!r}).")

        print("Deleting it...")
        deleted = credential_store.delete_login(SITE)
        if not deleted:
            print("FAIL: delete_login reported nothing to delete.")
            return 1

        after_delete = credential_store.get_login(SITE)
        if after_delete is not None:
            print("FAIL: entry still readable after delete_login.")
            return 1
        print("OK: entry confirmed gone after delete.")

    finally:
        credential_store.CONFIG_DIR = real_config_dir
        credential_store.LOGINS_FILE = real_logins_file
        credential_store.KEYCHAIN_SERVICE = real_service
        shutil.rmtree(temp_dir, ignore_errors=True)

    print("PASS: real Keychain seam verified end to end. Production service untouched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
