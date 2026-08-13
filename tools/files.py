import os
import subprocess

MAX_RESULTS = 15


def search_files(query, limit=MAX_RESULTS):

    home = os.path.expanduser("~")

    try:
        result = subprocess.run(
            ["mdfind", "-onlyin", home, query],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return "That search took too long — try a narrower query."

    if result.returncode != 0:
        return f"Could not search files: {result.stderr.strip()}"

    paths = [path for path in result.stdout.splitlines() if path][:limit]

    if not paths:
        return f"No files found matching '{query}'."

    return "\n".join(paths)


def open_file(path):

    result = subprocess.run(["open", path], capture_output=True, text=True, timeout=10)

    if result.returncode != 0:
        return f"Could not open '{path}': {result.stderr.strip()}"

    return f"Opened {path}"


if __name__ == "__main__":
    import sys
    print(search_files(sys.argv[1]))
