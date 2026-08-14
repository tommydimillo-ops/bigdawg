"""py2app build script for CampusPilotAgent.app -- exists solely so the
menu-bar process runs under its OWN code-signed bundle identity instead
of macOS's shared system Python.framework launcher. That shared launcher
has no way to declare "this app uses the microphone" without editing a
file Apple ships and code-signs itself -- doing that once, live, broke
Keychain access for the whole process (editing any file inside a signed
.app invalidates its signature, and Keychain's access-control checks
that signature before releasing stored secrets). A bundle we own and
sign ourselves doesn't have that problem.

Builds in "alias" mode (symlinks to this project's venv/source instead
of copying everything into the bundle) since this codebase is under
active development -- a full standalone build would need re-running
after every code change just to test something.

Build with:
    .venv/bin/python setup_app.py py2app -A

Then ad-hoc code-sign it (required for Keychain access to work at all --
see the module docstring above):
    codesign --force --deep --sign - dist/CampusPilotAgent.app
"""
from setuptools import setup

APP = ["ui/menu_bar.py"]

OPTIONS = {
    "argv_emulation": False,
    "iconfile": None,
    "plist": {
        "CFBundleName": "CampusPilotAgent",
        "CFBundleDisplayName": "CampusPilot",
        "CFBundleIdentifier": "com.tommy.campuspilot.jarvis",
        "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "1.0",
        "LSUIElement": True,
        "NSMicrophoneUsageDescription": (
            "CampusPilot listens for the wake word \"Jarvis\" and your "
            "spoken requests."
        ),
        "NSSpeechRecognitionUsageDescription": (
            "CampusPilot uses on-device Speech Recognition as a fallback "
            "for transcribing what you say if the primary cloud "
            "transcription service is unavailable."
        ),
    },
}

setup(
    app=APP,
    name="CampusPilotAgent",
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
