"""Build the historical sample: every FBS game, per-team box score + EPA, closing lines, and the
actual weather during the game window.

    python -m backtest.collect --seasons 2016-2025

Every API response is cached under backtest/cache/, so re-runs cost no API calls. Roughly 40 CFBD
calls per season (check your CFBD tier's monthly limit) and one Open-Meteo archive call per
venue per season.
"""
import argparse
import hashlib
import json
import os
import sqlite3
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import cfbd  # noqa: E402
import weather  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.getenv("BACKTEST_DB_PATH", os.path.join(HERE, "backtest.db"))
CACHE_DIR = os.getenv("BACKTEST_CACHE_DIR", os.path.join(HERE, "cache"))
ARCHIVE_URL = os.getenv("OPEN_METEO_ARCHIVE_URL", "https://archive-api.open-meteo.com/v1/archive")
HOURLY = "temperature_2m,precipitation,snowfall,wind_speed_10m,wind_gusts_10m,weather_code"

SCHEMA = """
CREATE TABLE IF NOT EXISTS bt_games (
    id INTEGER PRIMARY KEY, season INTEGER, week INTEGER, season_type TEXT, start_date TEXT,
    start_tbd INTEGER, home_team TEXT, away_team TEXT, home_points INTEGER, away_points INTEGER,
    neutral_site INTEGER, venue_id INTEGER,
    spread_close REAL, total_close REAL, spread_open REAL, total_open REAL, n_books INTEGER,
    conference_game INTEGER, attendance INTEGER
);
CREATE TABLE IF NOT EXISTS bt_team_games (
    game_id INTEGER, team TEXT, opponent TEXT, is_home INTEGER, points INTEGER,
    pass_att REAL, rush_att REAL, turnovers REAL,
    rush_yds REAL, pass_yds REAL, total_yds REAL, sacks REAL, tfl REAL,
    off_ppa REAL, off_sr REAL, off_expl REAL, pass_ppa REAL, pass_sr REAL, pass_expl REAL,
    rush_ppa REAL, rush_sr REAL, rush_expl REAL, def_ppa REAL, def_pass_ppa REAL, def_rush_ppa REAL,
    PRIMARY KEY (game_id, team)
);
CREATE TABLE IF NOT EXISTS bt_venues (id INTEGER PRIMARY KEY, name TEXT, latitude REAL, longitude REAL, dome INTEGER,
    capacity REAL, elevation REAL, grass INTEGER, timezone TEXT);
CREATE TABLE IF NOT EXISTS bt_teams (season INTEGER, school TEXT, conference TEXT, classification TEXT,
    latitude REAL, longitude REAL, timezone TEXT, venue_id INTEGER, PRIMARY KEY (season, school));
CREATE TABLE IF NOT EXISTS bt_rankings (season INTEGER, season_type TEXT, week INTEGER, poll TEXT, team TEXT, rank INTEGER,
    PRIMARY KEY (season, season_type, week, poll, team));
CREATE TABLE IF NOT EXISTS bt_talent (season INTEGER, team TEXT, talent REAL, PRIMARY KEY (season, team));
CREATE TABLE IF NOT EXISTS bt_sp (season INTEGER, team TEXT, rating REAL, ranking INTEGER, off REAL, def REAL, st REAL,
    PRIMARY KEY (season, team));
CREATE TABLE IF NOT EXISTS bt_coaches (season INTEGER, team TEXT, coach TEXT, hire_date TEXT, games INTEGER, wins INTEGER,
    losses INTEGER, ties INTEGER, preseason_rank INTEGER, postseason_rank INTEGER, srs REAL, sp_overall REAL,
    PRIMARY KEY (season, team, coach));
CREATE TABLE IF NOT EXISTS bt_weather (game_id INTEGER PRIMARY KEY, data TEXT);
"""

stats = {"cfbd_live": 0, "archive_live": 0, "cached": 0}


MIGRATIONS = {
    "bt_games": {"conference_game": "INTEGER", "attendance": "INTEGER"},
    "bt_team_games": {"rush_yds": "REAL", "pass_yds": "REAL", "total_yds": "REAL", "sacks": "REAL", "tfl": "REAL"},
    "bt_venues": {"capacity": "REAL", "elevation": "REAL", "grass": "INTEGER", "timezone": "TEXT"},
}


def connect(path=None):
    conn = sqlite3.connect(path or DB_PATH)
    conn.row_factory = sqlite3.Row
    for table, cols in MIGRATIONS.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, ddl in cols.items():
            if existing and col not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
    conn.executescript(SCHEMA)
    return conn


def _cache_path(kind, key):
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{kind}-{hashlib.sha1(key.encode()).hexdigest()[:16]}.json")


def cached_cfbd(path, max_age_hours=None, **params):
    """Cached CFBD call. max_age_hours=None caches forever (finished seasons and weeks)."""
    key = path + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
    fp = _cache_path("cfbd", key)
    fresh = max_age_hours is None or (os.path.exists(fp) and time.time() - os.path.getmtime(fp) < max_age_hours * 3600)
    if os.path.exists(fp) and fresh:
        stats["cached"] += 1
        with open(fp) as f:
            return json.load(f)
    data = cfbd._get(path, **params)
    stats["cfbd_live"] += 1
    with open(fp, "w") as f:
        json.dump(data, f)
    time.sleep(0.2)
    return data


def cached_archive(lat, lon, start, end):
    key = f"{lat:.4f},{lon:.4f},{start},{end}"
    fp = _cache_path("wx", key)
    if os.path.exists(fp):
        stats["cached"] += 1
        with open(fp) as f:
            return json.load(f)
    resp = requests.get(ARCHIVE_URL, params={
        "latitude": lat, "longitude": lon, "start_date": start, "end_date": end, "hourly": HOURLY,
        "temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "precipitation_unit": "inch",
        "timezone": "GMT",
    }, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    stats["archive_live"] += 1
    with open(fp, "w") as f:
        json.dump(data, f)
    time.sleep(0.3)
    return data


# ---------- parsing (pure) ----------

def consensus_line(lines):
    """Median across books of closing and opening spread/total (home perspective)."""
    def med(key, alt):
        vals = [cfbd._num(cfbd._f(l, key, alt)) for l in lines]
        vals = [v for v in vals if v is not None]
        return statistics.median(vals) if vals else None
    return {
        "spread_close": med("spread", "spread"),
        "total_close": med("overUnder", "over_under"),
        "spread_open": med("spreadOpen", "spread_open"),
        "total_open": med("overUnderOpen", "over_under_open"),
        "n_books": len(lines),
    }


def parse_box(raw):
    """/games/teams -> {(game_id, team): {...}}"""
    out = {}
    for g in raw:
        names = [cfbd._f(t, "team", "school") for t in g.get("teams") or []]
        for t in g.get("teams") or []:
            team = cfbd._f(t, "team", "school")
            st = {s.get("category"): s.get("stat") for s in t.get("stats") or []}
            comp_att = st.get("completionAttempts") or ""
            pass_att = None
            if "-" in str(comp_att):
                pass_att = cfbd._num(str(comp_att).replace("/", "-").split("-")[-1])
            out[(g["id"], team)] = {
                "is_home": 1 if cfbd._f(t, "homeAway", "home_away") == "home" else 0,
                "points": cfbd._num(t.get("points")),
                "pass_att": pass_att,
                "rush_att": cfbd._num(st.get("rushingAttempts")),
                "turnovers": cfbd._num(st.get("turnovers")),
                "rush_yds": cfbd._num(st.get("rushingYards")),
                "pass_yds": cfbd._num(st.get("netPassingYards")),
                "total_yds": cfbd._num(st.get("totalYards")),
                "sacks": cfbd._num(st.get("sacks")),
                "tfl": cfbd._num(st.get("tacklesForLoss")),
                "opponent": next((n for n in names if n != team), None),
            }
    return out


def parse_advanced(raw):
    """/stats/game/advanced -> {(game_id, team): {...}}"""
    out = {}
    for r in raw:
        o, d = r.get("offense") or {}, r.get("defense") or {}
        op, orr = cfbd._f(o, "passingPlays", "passing_plays") or {}, cfbd._f(o, "rushingPlays", "rushing_plays") or {}
        dp, dr = cfbd._f(d, "passingPlays", "passing_plays") or {}, cfbd._f(d, "rushingPlays", "rushing_plays") or {}
        n = cfbd._num
        out[(cfbd._f(r, "gameId", "game_id"), r.get("team"))] = {
            "opponent": r.get("opponent"),
            "off_ppa": n(o.get("ppa")), "off_sr": n(cfbd._f(o, "successRate", "success_rate")),
            "off_expl": n(o.get("explosiveness")),
            "pass_ppa": n(op.get("ppa")), "pass_sr": n(cfbd._f(op, "successRate", "success_rate")),
            "pass_expl": n(op.get("explosiveness")),
            "rush_ppa": n(orr.get("ppa")), "rush_sr": n(cfbd._f(orr, "successRate", "success_rate")),
            "rush_expl": n(orr.get("explosiveness")),
            "def_ppa": n(d.get("ppa")), "def_pass_ppa": n(dp.get("ppa")), "def_rush_ppa": n(dr.get("ppa")),
        }
    return out


# ---------- collection ----------

LIVE_TTL_HOURS = 6


def _week_is_final(games):
    """Every game final and the last kickoff 2+ days ago: its stats won't change."""
    if not games or not all(g["completed"] for g in games):
        return False
    last = max(weather._parse_kickoff(g["start_date"]) for g in games if g["start_date"])
    return datetime.now(timezone.utc) - last > timedelta(days=2)


def collect_season(conn, season):
    current = season >= datetime.now(timezone.utc).year
    ttl = LIVE_TTL_HOURS if current else None
    all_games = []
    for st in ("regular", "postseason"):
        all_games += [cfbd.parse_game(g, season=season, season_type=st)
                      for g in cached_cfbd("/games", max_age_hours=ttl, year=season, seasonType=st, classification="fbs")]
    games = [g for g in all_games if g["home_points"] is not None]
    scheduled = [g for g in all_games if g["home_points"] is None]  # kept for rest / look-ahead / schedule context

    lines = {}
    for st in ("regular", "postseason"):
        for g in cached_cfbd("/lines", max_age_hours=ttl, year=season, seasonType=st):
            lines[g["id"]] = consensus_line(g.get("lines") or [])

    for g in games + scheduled:
        conn.execute(
            """INSERT OR REPLACE INTO bt_games (id, season, week, season_type, start_date, start_tbd, home_team, away_team,
                   home_points, away_points, neutral_site, venue_id, spread_close, total_close, spread_open, total_open,
                   n_books, conference_game, attendance)
               VALUES (:id, :season, :week, :season_type, :start_date, :start_tbd,
                   :home_team, :away_team, :home_points, :away_points, :neutral_site, :venue_id,
                   :spread_close, :total_close, :spread_open, :total_open, :n_books, :conference_game, :attendance)""",
            {**g, **lines.get(g["id"], {"spread_close": None, "total_close": None, "spread_open": None,
                                         "total_open": None, "n_books": 0})},
        )

    weeks = sorted({(g["season_type"], g["week"]) for g in games})
    rows = 0
    for st, wk in weeks:
        wk_games = [g for g in all_games if g["season_type"] == st and g["week"] == wk]
        wk_ttl = None if (not current or _week_is_final(wk_games)) else LIVE_TTL_HOURS
        box = parse_box(cached_cfbd("/games/teams", max_age_hours=wk_ttl, year=season, week=wk, seasonType=st,
                                    classification="fbs"))
        adv = parse_advanced(cached_cfbd("/stats/game/advanced", max_age_hours=wk_ttl, year=season, week=wk, seasonType=st))
        for key in set(adv) | set(box):
            a, b = adv.get(key, {}), box.get(key, {})
            conn.execute(
                """INSERT OR REPLACE INTO bt_team_games (game_id, team, opponent, is_home, points, pass_att, rush_att,
                       turnovers, rush_yds, pass_yds, total_yds, sacks, tfl, off_ppa, off_sr, off_expl, pass_ppa, pass_sr,
                       pass_expl, rush_ppa, rush_sr, rush_expl, def_ppa, def_pass_ppa, def_rush_ppa)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (key[0], key[1], a.get("opponent") or b.get("opponent"), b.get("is_home"), b.get("points"),
                 b.get("pass_att"), b.get("rush_att"), b.get("turnovers"), b.get("rush_yds"), b.get("pass_yds"),
                 b.get("total_yds"), b.get("sacks"), b.get("tfl"), a.get("off_ppa"), a.get("off_sr"), a.get("off_expl"),
                 a.get("pass_ppa"), a.get("pass_sr"), a.get("pass_expl"), a.get("rush_ppa"), a.get("rush_sr"),
                 a.get("rush_expl"), a.get("def_ppa"), a.get("def_pass_ppa"), a.get("def_rush_ppa")),
            )
            rows += 1
    conn.commit()
    return len(games), rows


def parse_rankings(raw, season, season_type):
    out = []
    for wk in raw:
        for poll in wk.get("polls") or []:
            for r in poll.get("ranks") or []:
                team = cfbd._f(r, "school", "team")
                if team and r.get("rank"):
                    out.append((season, season_type, wk.get("week"), poll.get("poll"), team, int(r["rank"])))
    return out


def parse_coaches(raw, season):
    out = []
    for c in raw:
        name = f"{cfbd._f(c, 'firstName', 'first_name') or ''} {cfbd._f(c, 'lastName', 'last_name') or ''}".strip()
        for s in c.get("seasons") or []:
            if s.get("year") != season:
                continue
            n = cfbd._num
            out.append((season, s.get("school"), name, cfbd._f(c, "hireDate", "hire_date"), s.get("games"), s.get("wins"),
                        s.get("losses"), s.get("ties"), cfbd._f(s, "preseasonRank", "preseason_rank"),
                        cfbd._f(s, "postseasonRank", "postseason_rank"), n(s.get("srs")),
                        n(cfbd._f(s, "spOverall", "sp_overall"))))
    return out


def collect_context(conn, season):
    """Season-level context: teams + locations, polls, talent, SP+, head coaches (about 6 calls)."""
    ttl = LIVE_TTL_HOURS if season >= datetime.now(timezone.utc).year else None
    warnings = []

    def step(name, fn):
        try:
            fn()
        except Exception as e:  # one missing endpoint (e.g. a paid tier) shouldn't stop the rest
            warnings.append(f"{name}: {e}")

    def teams_():
        for t in cached_cfbd("/teams", max_age_hours=ttl, year=season):
            loc = t.get("location") or {}
            conn.execute("INSERT OR REPLACE INTO bt_teams VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (season, t.get("school"), t.get("conference"), t.get("classification"),
                          cfbd._num(loc.get("latitude")), cfbd._num(loc.get("longitude")), loc.get("timezone"),
                          cfbd._f(loc, "venueId", "venue_id", "id")))

    def rankings_():
        for st in ("regular", "postseason"):
            conn.executemany("INSERT OR REPLACE INTO bt_rankings VALUES (?, ?, ?, ?, ?, ?)",
                             parse_rankings(cached_cfbd("/rankings", max_age_hours=ttl, year=season, seasonType=st), season, st))

    def talent_():
        for t in cached_cfbd("/talent", max_age_hours=ttl, year=season):
            conn.execute("INSERT OR REPLACE INTO bt_talent VALUES (?, ?, ?)",
                         (season, cfbd._f(t, "team", "school"), cfbd._num(t.get("talent"))))

    def sp_():
        for r in cached_cfbd("/ratings/sp", max_age_hours=ttl, year=season):
            if not r.get("team") or r.get("team") == "nationalAverages":
                continue
            st = cfbd._f(r, "specialTeams", "special_teams") or {}
            conn.execute("INSERT OR REPLACE INTO bt_sp VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (season, r["team"], cfbd._num(r.get("rating")), r.get("ranking"),
                          cfbd._num((r.get("offense") or {}).get("rating")), cfbd._num((r.get("defense") or {}).get("rating")),
                          cfbd._num(st.get("rating"))))

    def coaches_():
        conn.executemany("INSERT OR REPLACE INTO bt_coaches VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         parse_coaches(cached_cfbd("/coaches", max_age_hours=ttl, year=season), season))

    for name, fn in (("teams", teams_), ("rankings", rankings_), ("talent", talent_), ("sp", sp_), ("coaches", coaches_)):
        step(name, fn)
    conn.commit()
    return warnings


def collect_venues(conn):
    for v in cached_cfbd("/venues"):
        loc = v.get("location") or {}
        lat = cfbd._num(v.get("latitude")) if v.get("latitude") is not None else cfbd._num(loc.get("x"))
        lon = cfbd._num(v.get("longitude")) if v.get("longitude") is not None else cfbd._num(loc.get("y"))
        conn.execute("""INSERT OR REPLACE INTO bt_venues (id, name, latitude, longitude, dome, capacity, elevation, grass, timezone)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                     (v["id"], v.get("name"), lat, lon, 1 if v.get("dome") else 0, cfbd._num(v.get("capacity")),
                      cfbd._num(v.get("elevation")), 1 if v.get("grass") else 0, v.get("timezone")))
    conn.commit()


def collect_weather(conn, seasons):
    """One archive call per (venue, season), then slice each game's window."""
    q = f"""SELECT g.id, g.season, g.start_date, v.id AS vid, v.latitude, v.longitude
            FROM bt_games g JOIN bt_venues v ON v.id = g.venue_id
            WHERE v.dome = 0 AND g.start_tbd = 0 AND g.start_date IS NOT NULL AND g.home_points IS NOT NULL
              AND v.latitude IS NOT NULL AND g.season IN ({",".join("?" * len(seasons))})
              AND g.id NOT IN (SELECT game_id FROM bt_weather)"""
    groups = {}
    for r in conn.execute(q, seasons).fetchall():
        groups.setdefault((r["vid"], r["season"]), []).append(r)

    done = 0
    for (vid, season), rows in groups.items():
        kicks = [weather._parse_kickoff(r["start_date"]).astimezone(timezone.utc) for r in rows]
        start = min(kicks).date().isoformat()
        end = (max(kicks) + timedelta(hours=weather.GAME_HOURS)).date().isoformat()
        try:
            data = cached_archive(rows[0]["latitude"], rows[0]["longitude"], start, end)
        except Exception as e:
            print(f"  weather failed for venue {vid} {season}: {e}", file=sys.stderr)
            continue
        for r, k in zip(rows, kicks):
            w = weather.forecast_window(data.get("hourly") or {}, k)
            if w:
                conn.execute("INSERT OR REPLACE INTO bt_weather VALUES (?, ?)", (r["id"], json.dumps(w)))
                done += 1
        conn.commit()
    return done


def parse_seasons(s):
    if "-" in s:
        a, b = s.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(x) for x in s.split(",")]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seasons", default="2016-2025", help="e.g. 2016-2025 or 2022,2023")
    ap.add_argument("--skip-weather", action="store_true")
    args = ap.parse_args(argv)
    seasons = parse_seasons(args.seasons)

    conn = connect()
    collect_venues(conn)
    for season in seasons:
        n_games, n_rows = collect_season(conn, season)
        warns = collect_context(conn, season)
        print(f"{season}: {n_games} games, {n_rows} team-game rows" + (f" (skipped: {'; '.join(warns)})" if warns else ""))
    if not args.skip_weather:
        print(f"weather: {collect_weather(conn, seasons)} games")
    print(f"API calls: CFBD {stats['cfbd_live']} live, archive {stats['archive_live']} live, {stats['cached']} from cache")
    print(f"Saved to {DB_PATH}. Next: python -m backtest.analyze")


if __name__ == "__main__":
    main()
