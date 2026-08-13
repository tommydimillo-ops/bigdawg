"""Manage API keys for CampusPilot, stored in the macOS Keychain instead of
the plaintext .env file. Run this directly in a terminal.

Usage:
    python -m tools.manage_secrets migrate   # copy keys from .env into the Keychain
    python -m tools.manage_secrets list      # show which keys are in the Keychain
"""

import argparse
import os

from dotenv import load_dotenv
load_dotenv()

from agent.secrets import has_secret_in_keychain, set_secret

KNOWN_KEYS = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY"]


def _migrate(args):
    for key in KNOWN_KEYS:
        value = os.getenv(key)

        if not value:
            print(f"{key}: not found in .env, skipping")
            continue

        if has_secret_in_keychain(key):
            confirm = input(f"{key} already in Keychain — overwrite with the .env value? [y/N] ")
            if confirm.strip().lower() != "y":
                print(f"{key}: kept existing Keychain value")
                continue

        set_secret(key, value)
        print(f"{key}: migrated to Keychain")


def _list(args):
    for key in KNOWN_KEYS:
        status = "in Keychain" if has_secret_in_keychain(key) else "not in Keychain (falls back to .env)"
        print(f"{key}: {status}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    migrate_parser = subparsers.add_parser("migrate", help="Copy keys from .env into the Keychain")
    migrate_parser.set_defaults(func=_migrate)

    list_parser = subparsers.add_parser("list", help="Show which keys are stored in the Keychain")
    list_parser.set_defaults(func=_list)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
