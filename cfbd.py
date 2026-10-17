"""Thin client for the CollegeFootballData.com API (https://api.collegefootballdata.com).

Handles both the current camelCase responses and the older snake_case ones.
"""
import os
from datetime import datetime, timezone

import requests

BASE_URL = os.getenv("CFBD_BASE_URL", "https://api.collegefootballdata.com")


class CFBDError(RuntimeError):
    pass


def _get(path, **params):
    key = os.getenv("CFBD_API_KEY")
    if not key:
        raise CFBDError("CFBD_API_KEY is not set in .env")
    resp = requests.get(
        f"{BASE_URL}{path}",
        params={k: v for k, v in params.items() if v is not None},
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise CFBDError(f"CFBD {path} returned {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def _f(d, *keys):
    """First present key among camelCase/snake_case variants."""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_game(g, season=None, week=None, season_type=None):
    return {
        "id": g["id"],
        "season": _f(g, "season") or season,
        "week": _f(g, "week") or week,
        "season_type": _f(g, "seasonType", "season_type") or season_type,
        "start_date": _f(g, "startDate", "start_date"),
        "start_tbd": 1 if _f(g, "startTimeTBD", "start_time_tbd") else 0,
        "home_team": _f(g, "homeTeam", "home_team"),
        "away_team": _f(g, "awayTeam", "away_team"),
        "home_conf": _f(g, "homeConference", "home_conference"),
        "away_conf": _f(g, "awayConference", "away_conference"),
        "neutral_site": 1 if _f(g, "neutralSite", "neutral_site") else 0,
        "conference_game": 1 if _f(g, "conferenceGame", "conference_game") else 0,
        "attendance": _f(g, "attendance"),
        "venue": _f(g, "venue"),
        "venue_id": _f(g, "venueId", "venue_id"),
        "home_points": _f(g, "homePoints", "home_points"),
        "away_points": _f(g, "awayPoints", "away_points"),
        "completed": 1 if _f(g, "completed") else 0,
    }


def fetch_games(season, week, season_type="regular"):
    raw = _get("/games", year=season, week=week, seasonType=season_type, classification="fbs")
    games = []
    for g in raw:
        game = parse_game(g, season, week, season_type)
        for k in ("start_tbd", "conference_game", "attendance"):
            game.pop(k)
        games.append(game)
    return games


def pick_line(lines, preferred_provider=None):
    """Choose one sportsbook line: the preferred provider if present, else the first with a spread."""
    usable = [l for l in lines if _num(_f(l, "spread")) is not None or _num(_f(l, "overUnder", "over_under")) is not None]
    if not usable:
        return None
    chosen = None
    if preferred_provider:
        chosen = next((l for l in usable if (l.get("provider") or "").lower() == preferred_provider.lower()), None)
    chosen = chosen or usable[0]
    return {
        "provider": chosen.get("provider"),
        "spread": _num(_f(chosen, "spread")),
        "total": _num(_f(chosen, "overUnder", "over_under")),
        "spread_open": _num(_f(chosen, "spreadOpen", "spread_open")),
        "total_open": _num(_f(chosen, "overUnderOpen", "over_under_open")),
    }


def fetch_lines(season, week, season_type="regular", preferred_provider=None):
    """Return {game_id: line_dict} for the week."""
    raw = _get("/lines", year=season, week=week, seasonType=season_type)
    out = {}
    for g in raw:
        line = pick_line(g.get("lines") or [], preferred_provider)
        if line:
            out[g["id"]] = line
    return out


def current_week(season, now=None):
    """Find (week, season_type) for today from /calendar, or None if out of season."""
    now = now or datetime.now(timezone.utc)
    weeks = _get("/calendar", year=season)

    def parse(s):
        return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None

    upcoming = []
    for w in weeks:
        start = parse(_f(w, "startDate", "firstGameStart", "first_game_start"))
        end = parse(_f(w, "endDate", "lastGameStart", "last_game_start"))
        if not start or not end:
            continue
        stype = _f(w, "seasonType", "season_type") or "regular"
        if start <= now <= end:
            return {"week": w["week"], "season_type": stype}
        if start > now:
            upcoming.append((start, w["week"], stype))
    if upcoming:
        _, week, stype = min(upcoming)
        return {"week": week, "season_type": stype}
    return None
