import subprocess
from datetime import datetime

from agent.disk_health import check_disk_health, format_disk_warning


def get_system_status():

    # An explicit, unambiguous current-time line — previously the model
    # had no reliable way to answer "what time is it" and would guess or
    # hedge (it could only infer a rough time indirectly from the uptime
    # line below, which isn't its job to parse).
    lines = ["Current time: " + datetime.now().strftime("%I:%M %p on %A, %B %d, %Y").lstrip("0")]

    battery = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True).stdout
    for line in battery.splitlines():
        if "%" in line:
            lines.append("Battery: " + line.strip().split("\t")[-1])

    disk = subprocess.run(["df", "-h", "/"], capture_output=True, text=True).stdout.splitlines()
    if len(disk) > 1:
        parts = disk[1].split()
        lines.append(f"Disk: {parts[3]} free of {parts[1]} ({parts[4]} used)")

    disk_warning = format_disk_warning(check_disk_health())
    if disk_warning:
        lines.append(disk_warning)

    uptime = subprocess.run(["uptime"], capture_output=True, text=True).stdout.strip()
    if uptime:
        lines.append("Uptime: " + uptime)

    wifi = subprocess.run(["ipconfig", "getsummary", "en0"], capture_output=True, text=True).stdout
    for line in wifi.splitlines():
        if line.strip().startswith("SSID"):
            lines.append("Wi-Fi: " + line.split(":", 1)[1].strip())
            break

    return "\n".join(lines) if lines else "Couldn't read system status."


if __name__ == "__main__":
    print(get_system_status())
