import json
import os

import keyring

# The password itself lives only in the macOS Keychain (via keyring),
# never in a file. This JSON file holds non-secret metadata only: which
# site nicknames exist, which domain each one is allowed to autofill on,
# and the username to pair with the Keychain lookup.
CONFIG_DIR = os.path.expanduser("~/Library/Application Support/CampusPilot")
LOGINS_FILE = os.path.join(CONFIG_DIR, "logins.json")

KEYCHAIN_SERVICE = "CampusPilot"


def _load_metadata():
    if os.path.exists(LOGINS_FILE):
        with open(LOGINS_FILE, "r") as file:
            return json.load(file)
    return {}


def _save_metadata(metadata):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp_file = f"{LOGINS_FILE}.tmp"
    with open(tmp_file, "w") as file:
        json.dump(metadata, file, indent=4)
    os.replace(tmp_file, LOGINS_FILE)


def save_login(site, domain, username, password):
    site = site.strip().lower()

    metadata = _load_metadata()
    metadata[site] = {"domain": domain.strip().lower(), "username": username.strip()}
    _save_metadata(metadata)

    keyring.set_password(KEYCHAIN_SERVICE, f"{site}:{username.strip()}", password)


def get_login(site):
    site = site.strip().lower()
    metadata = _load_metadata()

    entry = metadata.get(site)
    if not entry:
        return None

    password = keyring.get_password(KEYCHAIN_SERVICE, f"{site}:{entry['username']}")
    if password is None:
        return None

    return {"domain": entry["domain"], "username": entry["username"], "password": password}


def list_logins():
    metadata = _load_metadata()
    return {site: entry["username"] for site, entry in metadata.items()}


def delete_login(site):
    site = site.strip().lower()
    metadata = _load_metadata()

    entry = metadata.pop(site, None)
    if not entry:
        return False

    _save_metadata(metadata)
    try:
        keyring.delete_password(KEYCHAIN_SERVICE, f"{site}:{entry['username']}")
    except keyring.errors.PasswordDeleteError:
        pass

    return True
