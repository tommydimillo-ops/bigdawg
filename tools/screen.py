import pyautogui
from datetime import datetime

# OpenAI's vision pipeline downscales to a ~1568px-long-edge equivalent
# server-side anyway, so capturing full Retina resolution just costs
# upload time and disk space for no quality benefit.
MAX_DIMENSION = 1568


def take_screenshot():
    filename = f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S%f')}.jpg"

    screenshot = pyautogui.screenshot()
    screenshot.thumbnail((MAX_DIMENSION, MAX_DIMENSION))
    screenshot.convert("RGB").save(filename, "JPEG", quality=85)

    return filename


if __name__ == "__main__":
    image = take_screenshot()
    print(f"Screenshot saved: {image}")