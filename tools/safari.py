import subprocess


def open_safari(url):

    escaped_url = url.replace("\\", "\\\\").replace('"', '\\"')

    script = f'''
    tell application "Safari"
        activate
        open location "{escaped_url}"
    end tell
    '''

    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        return f"Could not open Safari: {result.stderr.strip()}"

    return f"Opened Safari to {url}"


if __name__ == "__main__":

    print(
        open_safari("https://www.google.com")
    )