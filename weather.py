"""Kickoff weather from Open-Meteo (free, no key) using CFBD venue coordinates."""
import os
from datetime import datetime, timedelta, timezone

import requests

from cfbd import _f, _get, _num

OPEN_METEO_URL = os.getenv("OPEN_METEO_URL", "https://api.open-meteo.com/v1/forecast")
FORECAST_DAYS = 16
GAME_HOURS = 4  # kickoff hour + the next 3


def fetch_venues():
    """Return {venue_id: {...}} from CFBD /venues."""
    out = {}
    for v in _get("/venues"):
        loc = v.get("location") or {}
        lat = _num(_f(v, "latitude")) if _f(v, "latitude") is not None else _num(loc.get("x"))
        lon = _num(_f(v, "longitude")) if _f(v, "longitude") is not None else _num(loc.get("y"))
        out[v["id"]] = {
            "id": v["id"],
            "name": v.get("name"),
            "latitude": lat,
            "longitude": lon,
            "elevation": _num(v.get("elevation")),
            "dome": 1 if v.get("dome") else 0,
            "grass": 1 if v.get("grass") else 0,
        }
    return out


def _parse_kickoff(start_date):
    k = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
    return k if k.tzinfo else k.replace(tzinfo=timezone.utc)


def forecast_window(hourly, kickoff):
    """Summarize Open-Meteo hourly arrays over the game window. Pure function."""
    times = hourly.get("time") or []
    start = kickoff.replace(minute=0, second=0, microsecond=0, tzinfo=None)
    window = {start + timedelta(hours=h) for h in range(GAME_HOURS)}
    idx = [i for i, t in enumerate(times) if datetime.fromisoformat(t) in window]
    if not idx:
        return None

    def vals(key):
        arr = hourly.get(key) or []
        return [arr[i] for i in idx if i < len(arr) and arr[i] is not None]

    def agg(key, fn, nd=1):
        v = vals(key)
        return round(fn(v), nd) if v else None

    return {
        "temp_f": agg("temperature_2m", lambda v: v[0]),
        "temp_min_f": agg("temperature_2m", min),
        "temp_max_f": agg("temperature_2m", max),
        "wind_mph": agg("wind_speed_10m", max),
        "gust_mph": agg("wind_gusts_10m", max),
        "precip_in": agg("precipitation", sum, 2),
        "precip_max_hr_in": agg("precipitation", max, 2),
        "precip_prob": agg("precipitation_probability", max, 0),
        "snow_in": agg("snowfall", sum, 2),
        "weather_code": agg("weather_code", max, 0),
    }


def fetch_forecast(lat, lon, start_date, now=None):
    """Forecast for the game window, or None if kickoff is past/too far out."""
    if lat is None or lon is None or not start_date:
        return None
    kickoff = _parse_kickoff(start_date)
    now = now or datetime.now(timezone.utc)
    if kickoff < now - timedelta(hours=GAME_HOURS) or kickoff > now + timedelta(days=FORECAST_DAYS - 1):
        return None
    k = kickoff.astimezone(timezone.utc)
    resp = requests.get(OPEN_METEO_URL, params={
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m,precipitation,precipitation_probability,snowfall,wind_speed_10m,wind_gusts_10m,weather_code",
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "GMT",
        "start_date": k.date().isoformat(),
        "end_date": (k + timedelta(hours=GAME_HOURS)).date().isoformat(),
    }, timeout=20)
    resp.raise_for_status()
    summary = forecast_window(resp.json().get("hourly") or {}, k)
    if summary:
        summary["fetched_at"] = now.isoformat(timespec="minutes")
    return summary


PREVIOUS_RUNS_URL = os.getenv("OPEN_METEO_PREVIOUS_RUNS_URL", "https://previous-runs-api.open-meteo.com/v1/forecast")
HOURLY_VARS = ["temperature_2m", "precipitation", "precipitation_probability", "snowfall", "wind_speed_10m",
               "wind_gusts_10m", "weather_code"]


def forecast_as_of(lat, lon, start_date, cutoff):
    """The forecast for the game window as it stood before `cutoff`, from Open-Meteo's Previous Runs API
    (variables suffixed _previous_dayN = forecast issued N days earlier). Returns None if unavailable."""
    if lat is None or lon is None or not start_date:
        return None
    kick = _parse_kickoff(start_date).astimezone(timezone.utc)
    lead = max(1, min(7, int(-(-(kick - cutoff).total_seconds() // 86400))))   # ceil(days), 1..7
    names = [f"{v}_previous_day{lead}" for v in HOURLY_VARS]
    resp = requests.get(PREVIOUS_RUNS_URL, params={
        "latitude": lat, "longitude": lon, "hourly": ",".join(names), "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph", "precipitation_unit": "inch", "timezone": "GMT",
        "start_date": kick.date().isoformat(), "end_date": (kick + timedelta(hours=GAME_HOURS)).date().isoformat()}, timeout=20)
    resp.raise_for_status()
    hourly = resp.json().get("hourly") or {}
    renamed = {"time": hourly.get("time") or []}
    for v, n in zip(HOURLY_VARS, names):
        renamed[v] = hourly.get(n) or []
    summary = forecast_window(renamed, kick)
    if summary:
        summary["issued_days_before"] = lead
        summary["source"] = "previous-runs forecast"
    return summary
