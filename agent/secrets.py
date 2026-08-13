import os

import keyring

SERVICE = "CampusPilot-APIKeys"


def get_secret(name):
    """Prefer the Keychain; fall back to an environment variable (e.g. from
    .env) so this keeps working before the one-time migration, or if a key
    is only ever set via the environment for some other reason."""
    value = keyring.get_password(SERVICE, name)
    if value:
        return value
    return os.getenv(name)


def set_secret(name, value):
    keyring.set_password(SERVICE, name, value)


def has_secret_in_keychain(name):
    return keyring.get_password(SERVICE, name) is not None
