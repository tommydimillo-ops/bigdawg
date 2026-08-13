"""Manage saved logins for CampusPilot's browser autofill.

Run this directly in a terminal — it is intentionally NOT exposed as a
chat/agent tool. Passwords are typed here via a hidden prompt and stored
only in the macOS Keychain; they never appear in chat, in a file, or in any
request sent to Claude/OpenAI.

Usage:
    python -m tools.manage_logins add <site> <domain> <username>
    python -m tools.manage_logins list
    python -m tools.manage_logins remove <site>
"""

import argparse
import getpass

from tools.credential_store import delete_login, get_login, list_logins, save_login


def _add(args):
    if get_login(args.site):
        confirm = input(f"'{args.site}' already has a saved login — overwrite it? [y/N] ")
        if confirm.strip().lower() != "y":
            print("Cancelled.")
            return

    password = getpass.getpass(f"Password for {args.username}: ")
    if not password:
        print("No password entered — cancelled.")
        return

    save_login(args.site, args.domain, args.username, password)
    print(f"Saved '{args.site}' (domain: {args.domain}, user: {args.username}) to Keychain.")


def _list(args):
    logins = list_logins()
    if not logins:
        print("No saved logins.")
        return
    for site, username in logins.items():
        print(f"{site}: {username}")


def _remove(args):
    if delete_login(args.site):
        print(f"Removed '{args.site}'.")
    else:
        print(f"No saved login named '{args.site}'.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="Save a login")
    add_parser.add_argument("site", help="A short nickname, e.g. 'brightspace'")
    add_parser.add_argument(
        "domain",
        help=(
            "The domain of the page where you actually type your password "
            "(e.g. 'login.microsoftonline.com' for a school SSO login — "
            "not the school's main website)."
        ),
    )
    add_parser.add_argument("username", help="Your username/email for this login")
    add_parser.set_defaults(func=_add)

    list_parser = subparsers.add_parser("list", help="List saved logins (no passwords shown)")
    list_parser.set_defaults(func=_list)

    remove_parser = subparsers.add_parser("remove", help="Delete a saved login")
    remove_parser.add_argument("site")
    remove_parser.set_defaults(func=_remove)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
