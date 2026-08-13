import json
import subprocess
from datetime import datetime

TIMEOUT_SECONDS = 8


def get_weather(location=None):
    """Real weather via wttr.in — a plain HTTP text service, not a
    browser, so it can't hit a CAPTCHA/human-verification wall the way
    browser-based weather lookups did (which would derail the wake-up
    greeting mid-sentence). Also proved more reliably geo-located in
    testing than a generic web search — a browser search once resolved
    "weather" to Newark, Ohio; this correctly resolved to Tampa."""

    query = location.strip().replace(" ", "+") if location else ""
    url = f"https://wttr.in/{query}?format=j1"

    try:
        result = subprocess.run(
            ["curl", "-s", "--max-time", str(TIMEOUT_SECONDS), url],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS + 2,
        )
        data = json.loads(result.stdout)
    except Exception as error:
        return f"Couldn't fetch weather: {error}"

    try:
        area = data["nearest_area"][0]
        location_name = f"{area['areaName'][0]['value']}, {area['region'][0]['value']}"
        current = data["current_condition"][0]

        lines = [
            f"Location: {location_name}",
            f"Now: {current['temp_F']}°F, {current['weatherDesc'][0]['value']} "
            f"(feels like {current['FeelsLikeF']}°F)",
            "Next few hours:",
        ]

        now_hour = datetime.now().hour
        upcoming = [
            hour for hour in data["weather"][0]["hourly"]
            if int(hour["time"].zfill(4)[:2]) >= now_hour
        ]
        if len(upcoming) < 3:
            # Late in the day — top up with tomorrow's early hours instead
            # of showing just one lonely block.
            upcoming += data["weather"][1]["hourly"]
        upcoming = upcoming[:3]

        for hour in upcoming:
            hour_num = int(hour["time"].zfill(4)[:2])
            lines.append(
                f"  {hour_num:02d}:00 - {hour['tempF']}°F, "
                f"{hour['weatherDesc'][0]['value'].strip()}, "
                f"{hour['chanceofrain']}% chance of rain"
            )

        return "\n".join(lines)

    except (KeyError, IndexError) as error:
        return f"Got a weather response but couldn't parse it: {error}"


WMO_DESCRIPTIONS = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "depositing rime fog",
    51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
    56: "light freezing drizzle", 57: "dense freezing drizzle",
    61: "slight rain", 63: "moderate rain", 65: "heavy rain",
    66: "light freezing rain", 67: "heavy freezing rain",
    71: "slight snow", 73: "moderate snow", 75: "heavy snow", 77: "snow grains",
    80: "slight rain showers", 81: "moderate rain showers", 82: "violent rain showers",
    85: "slight snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with slight hail", 99: "thunderstorm with heavy hail",
}


def _resolve_location(location):
    """Returns (label, lat, lon). With a location name, geocodes it via
    Open-Meteo's free geocoding API. With none, reuses wttr.in's IP-based
    geolocation (same source get_weather already relies on) so both tools
    agree on "here" without needing a second geolocation mechanism."""

    if location:
        query = location.strip().replace(" ", "+")
        url = f"https://geocoding-api.open-meteo.com/v1/search?name={query}&count=1"
        result = subprocess.run(
            ["curl", "-s", "--max-time", str(TIMEOUT_SECONDS), url],
            capture_output=True, text=True, timeout=TIMEOUT_SECONDS + 2,
        )
        data = json.loads(result.stdout)
        match = data.get("results", [None])[0]
        if not match:
            return None, None, None
        label = f"{match['name']}, {match.get('admin1', match.get('country', ''))}"
        return label, match["latitude"], match["longitude"]

    result = subprocess.run(
        ["curl", "-s", "--max-time", str(TIMEOUT_SECONDS), "https://wttr.in/?format=j1"],
        capture_output=True, text=True, timeout=TIMEOUT_SECONDS + 2,
    )
    data = json.loads(result.stdout)
    area = data["nearest_area"][0]
    label = f"{area['areaName'][0]['value']}, {area['region'][0]['value']}"
    return label, float(area["latitude"]), float(area["longitude"])


def get_weekly_forecast(location=None):
    """7-day forecast via Open-Meteo — a direct JSON API like get_weather,
    so no browser/CAPTCHA risk. wttr.in (used for get_weather's current
    conditions) only returns 3 days on its free tier, not enough for a real
    week-ahead view, so this uses a second source specifically for range."""

    try:
        label, lat, lon = _resolve_location(location)
    except Exception as error:
        return f"Couldn't resolve that location: {error}"

    if lat is None:
        return f"Couldn't find a location matching '{location}'."

    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&daily=weathercode,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
        "&temperature_unit=fahrenheit&forecast_days=7&timezone=auto"
    )

    try:
        result = subprocess.run(
            ["curl", "-s", "--max-time", str(TIMEOUT_SECONDS), url],
            capture_output=True, text=True, timeout=TIMEOUT_SECONDS + 2,
        )
        data = json.loads(result.stdout)
        daily = data["daily"]
    except Exception as error:
        return f"Couldn't fetch the forecast: {error}"

    lines = [f"7-day forecast for {label}:"]

    for i, date_str in enumerate(daily["time"]):
        weekday = datetime.strptime(date_str, "%Y-%m-%d").strftime("%A")
        high = round(daily["temperature_2m_max"][i])
        low = round(daily["temperature_2m_min"][i])
        rain = daily["precipitation_probability_max"][i]
        description = WMO_DESCRIPTIONS.get(daily["weathercode"][i], "unknown conditions")
        lines.append(f"  {weekday} ({date_str}): {high}°F / {low}°F, {description}, {rain}% chance of rain")

    return "\n".join(lines)


if __name__ == "__main__":
    print(get_weather())
    print()
    print(get_weekly_forecast())
