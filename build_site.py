"""Build the website's data files (docs/data/) from CollegeFootballData, Open-Meteo and the history warehouse.

Runs on GitHub Actions on a schedule (see .github/workflows/update-data.yml). It needs CFBD_API_KEY.

For every week it touches it writes docs/data/<season>/<type>-w<NN>.json with each game's line, opening line,
result, box score, weather and a pre-written "context" (analytics, weather vs style, betting history,
situation, home field, coaching, FCS history) that the site pastes into Claude prompts.

The cutoff rule: a week's context is rebuilt on every run until the pool deadline (Friday 12:00 PM Pacific
before the week's main Saturday). After that it is frozen, so a card is always predicted with what was known
by the deadline. A past week built for the first time after its deadline gets a strict as-of-deadline replay
(stats from earlier games only, opening line, the forecast issued before the cutoff).

    python build_site.py                     # current week + last week, refresh unfinished weeks
    python build_site.py --weeks 7           # also (re)build week 7 of the current season
    python build_site.py --season 2026 --weeks 6,7 --history 2022-2025
"""
import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.getenv("SITE_CACHE_DIR", os.path.join(HERE, ".cache"))
os.makedirs(CACHE, exist_ok=True)
# Point the app's databases at the cache folder before any app module is imported.
os.environ.setdefault("CFB_DB_PATH", os.path.join(CACHE, "picks.db"))
os.environ.setdefault("BACKTEST_DB_PATH", os.path.join(CACHE, "backtest.db"))
os.environ.setdefault("BACKTEST_CACHE_DIR", os.path.join(CACHE, "cfbd"))

import cfbd  # noqa: E402
import conditions  # noqa: E402

OUT = os.getenv("SITE_DATA_DIR", os.path.join(HERE, "docs", "data"))
TZ = ZoneInfo(os.getenv("PICKS_TZ", "America/Los_Angeles"))
PROVIDER = os.getenv("LINES_PROVIDER") or "DraftKings"

# ---------- CFBD call budget: the free tier allows about 1,000 calls a month ----------

TTL_HOURS = [  # longest-prefix match on the path; None = keep forever
    ("/games/teams", 1), ("/stats/game/advanced", 1), ("/games", 1), ("/lines", 1),
    ("/rankings", 20), ("/ratings/sp", 20), ("/stats/season/advanced", 20), ("/stats/season", 20),
    ("/calendar", 24 * 7), ("/teams/fbs", 24 * 7), ("/teams", 24 * 7), ("/venues", 24 * 30),
    ("/coaches", 24 * 7), ("/talent", 24 * 7),
]
CALLS = {"live": 0, "cached": 0}
_raw_get = cfbd._get


def _ttl(path):
    best = None
    for prefix, hours in TTL_HOURS:
        if path.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, hours)
    return best[1] if best else 1


def budget_get(path, **params):
    """cfbd._get with a disk cache, so one run never asks CFBD for the same thing twice."""
    import hashlib
    key = path + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params) if params[k] is not None)
    fp = os.path.join(CACHE, "api", hashlib.sha1(key.encode()).hexdigest()[:20] + ".json")
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    ttl = _ttl(path)
    if os.path.exists(fp) and (ttl is None or time.time() - os.path.getmtime(fp) < ttl * 3600):
        CALLS["cached"] += 1
        with open(fp) as f:
            return json.load(f)
    data = _raw_get(path, **params)
    CALLS["live"] += 1
    with open(fp, "w") as f:
        json.dump(data, f)
    return data


cfbd._get = budget_get
for _mod in ("analytics", "teams", "weather"):   # modules that did `from cfbd import _get`
    __import__(_mod)
    if hasattr(sys.modules[_mod], "_get"):
        sys.modules[_mod]._get = budget_get

import app as picks_app  # noqa: E402  (creates the tables; gives build_context and friends)
import db  # noqa: E402
import history  # noqa: E402
import manual  # noqa: E402
import pool_api  # noqa: E402
import predict  # noqa: E402
import review  # noqa: E402
import teams as teams_mod  # noqa: E402
from backtest import collect  # noqa: E402

BOX_KEYS = ("points", "rush_yds", "pass_yds", "total_yds", "sacks", "turnovers")


def now_utc():
    return datetime.now(timezone.utc)


def parse_iso(s):
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def deadline_for(games):
    """Friday 12:00 PM Pacific before the week's main Saturday (most common Saturday among kickoffs)."""
    sats = []
    for g in games:
        if g.get("start_date"):
            local = parse_iso(g["start_date"]).astimezone(TZ)
            sats.append((local + timedelta(days=(5 - local.weekday()) % 7)).date())
    if not sats:
        return None
    sat = max(set(sats), key=sats.count)
    return datetime.combine(sat - timedelta(days=1), dtime(12, 0), tzinfo=TZ).astimezone(timezone.utc)


def week_file(season, week, season_type):
    return os.path.join(OUT, str(season), f"{season_type}-w{int(week):02d}.json")


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"), default=str)
    os.replace(tmp, path)


# ---------- history warehouse ----------

def refresh_history(season, history_seasons):
    hconn = collect.connect(history.DB_PATH)
    warnings = []
    if not hconn.execute("SELECT 1 FROM bt_venues LIMIT 1").fetchone():
        collect.collect_venues(hconn)
    for s in sorted(set(history_seasons) | {season}):
        try:
            collect.collect_season(hconn, s)
            warnings += [f"{s} {w}" for w in collect.collect_context(hconn, s)]
        except Exception as e:
            warnings.append(f"history {s}: {e}")
    hconn.close()
    return warnings


def maybe_analyze(force=False):
    """Weather calibration + home-field numbers from completed seasons (weekly, or when missing)."""
    cal = picks_app.CALIBRATION_PATH
    fresh = os.path.exists(cal) and time.time() - os.path.getmtime(cal) < 6 * 86400
    if fresh and not force:
        return None
    try:
        hconn = collect.connect(history.DB_PATH)
        seasons = [r[0] for r in hconn.execute("SELECT DISTINCT season FROM bt_games")]
        collect.collect_weather(hconn, [s for s in seasons if s < now_utc().year])
        hconn.close()
        from backtest import analyze
        analyze.main(["--out", os.path.dirname(cal)])
        return "analysis refreshed"
    except BaseException as e:   # analyze calls sys.exit when there's no weather yet
        return f"analysis skipped: {e}"


# ---------- one week ----------

def week_games_from_season(season, week, season_type):
    """This week's games and lines out of the season-wide responses (no extra CFBD calls)."""
    raw = cfbd._get("/games", year=season, seasonType=season_type, classification="fbs")
    games = [cfbd.parse_game(g, season, None, season_type) for g in raw]
    games = [g for g in games if int(g["week"] or 0) == int(week)]
    for g in games:
        for k in ("start_tbd", "conference_game", "attendance"):
            g.pop(k, None)
    lines = {}
    for g in cfbd._get("/lines", year=season, seasonType=season_type):
        line = cfbd.pick_line(g.get("lines") or [], PROVIDER)
        if line:
            lines[g["id"]] = line
    return games, lines


def box_scores(season, week, season_type, games):
    if not any(g["completed"] for g in games):
        return {}
    try:
        return collect.parse_box(cfbd._get("/games/teams", year=season, week=week, seasonType=season_type,
                                           classification="fbs"))
    except Exception as e:
        print(f"  box scores unavailable: {e}")
        return {}


def prop_stats(ctx):
    h = ctx.get("history")
    if not h:
        return None
    out = {}
    for side in ("home", "away"):
        t = h["teams"][side]
        st = t["stats"]
        out[side] = {"team": t["team"], "for": st["for"], "against": st["against"], "games": st["games"][-6:]}
    return out


def weather_text(ctx):
    return conditions.describe(ctx["assessment"]) if ctx.get("assessment") else None


def build_week(conn, season, week, season_type, first_run_extras):
    path = week_file(season, week, season_type)
    old = load_json(path, {}) or {}
    old_games = {g["id"]: g for g in old.get("games", [])}
    games, lines = week_games_from_season(season, week, season_type)
    if not games:
        print(f"  no games for {season} {season_type} week {week}")
        return None
    for g in games:
        db.upsert_game(conn, g)
        if g["id"] in lines:
            db.update_line(conn, g["id"], lines[g["id"]])
    conn.commit()
    warnings = []
    if first_run_extras:
        _, warnings = picks_app.refresh_week_extras(conn, season, games)
        conn.commit()

    deadline = deadline_for(games)
    live = deadline is None or now_utc() < deadline
    box = box_scores(season, week, season_type, games)
    out_games = []
    for g in games:
        row = dict(conn.execute("SELECT * FROM games WHERE id=?", (g["id"],)).fetchone())
        prev = old_games.get(g["id"], {})
        ctx_out = prev.get("ctx")
        kicked = picks_app._kicked_off(row)
        if live and not kicked:
            ctx = picks_app.build_context(conn, row, full=True)
            ctx_out = {"as_of": ctx["as_of"], "replay": False, "frozen": False,
                       "line": {"spread": row["spread"], "total": row["total"], "label": "line at the time"},
                       "text": "\n".join(predict.context_lines(row, ctx)).strip("\n"),
                       "prop_stats": prop_stats(ctx), "audit": [], "weather_text": weather_text(ctx)}
            summary = {"assessment": ctx["assessment"],
                       "profiles": {"home": picks_app._profile_summary(ctx["home_profile"]),
                                    "away": picks_app._profile_summary(ctx["away_profile"])},
                       "history": picks_app._history_summary(ctx["history"])}
        else:
            summary = prev.get("summary")
            if ctx_out and not ctx_out.get("frozen"):
                ctx_out["frozen"] = True
            if not ctx_out and deadline is not None:   # first build after the deadline: strict replay as of it
                try:
                    ctx_out, summary = replay_context(conn, row, deadline)
                except Exception as e:
                    print(f"  replay failed for {row['away_team']} at {row['home_team']}: {e}")
        b = {}
        for side, team in (("home", row["home_team"]), ("away", row["away_team"])):
            got = box.get((row["id"], team))
            if got:
                b[side] = {k: got.get(k) for k in BOX_KEYS}
        out_games.append({
            **{k: row[k] for k in ("id", "season", "week", "season_type", "start_date", "home_team", "away_team",
                                   "home_conf", "away_conf", "neutral_site", "venue", "venue_id", "home_points",
                                   "away_points", "completed", "spread", "total", "spread_open", "total_open", "provider")},
            "weather": (json.loads(row["weather"]) if row.get("weather") else
                        ((summary or {}).get("assessment") or {}).get("weather") or prev.get("weather")),
            "summary": summary, "box": b or prev.get("box") or None, "ctx": ctx_out,
        })
    out_games.sort(key=lambda g: (g["start_date"] or "", g["id"]))
    data = {"season": season, "week": int(week), "season_type": season_type,
            "generated_at": now_utc().isoformat(timespec="minutes"),
            "deadline": deadline.isoformat(timespec="minutes") if deadline else None,
            "frozen": not live, "complete": all(g["completed"] for g in out_games),
            "warnings": warnings, "games": out_games}
    write_json(path, data)
    n_ctx = sum(1 for g in out_games if g["ctx"])
    print(f"  {season} {season_type} week {week}: {len(out_games)} games, {n_ctx} with context, "
          f"{'live' if live else 'frozen at deadline'}")
    return data


def replay_context(conn, row, deadline):
    jobs = pool_api._replay_jobs(conn, row)
    overrides = pool_api._gather_replay(row, jobs, deadline, db.tx, searches=False)
    ctx = picks_app.build_context(conn, row, as_of=deadline, replay=True, overrides=overrides, full=True)
    out = {"as_of": ctx["as_of"], "replay": True, "frozen": True,
           "line": {"spread": row["spread_open"], "total": row["total_open"], "label": "opening line (replay: closing line hidden)"},
           "text": "\n".join(predict.context_lines(row, ctx)).strip("\n"),
           "prop_stats": prop_stats(ctx), "audit": ctx.get("audit", []), "weather_text": weather_text(ctx)}
    summary = {"assessment": ctx["assessment"],
               "profiles": {"home": picks_app._profile_summary(ctx["home_profile"]),
                            "away": picks_app._profile_summary(ctx["away_profile"])},
               "history": picks_app._history_summary(ctx["history"])}
    return out, summary


# ---------- shared files ----------

def write_prompts():
    write_json(os.path.join(OUT, "prompts.json"), {
        "system": predict.SYSTEM, "prop_system": predict.PROP_SYSTEM, "review_system": review.REVIEW_SYSTEM,
        "review_schema": review._schema(0), "game_spec": manual.GAME_SPEC, "prop_spec": manual.PROP_SPEC,
        "stat_labels": predict.STAT_LABELS, "miss_categories": review.MISS_CATEGORIES,
    })


def write_reference(season):
    ref = teams_mod.load_reference(season) or {}
    nick = load_json(os.path.join(HERE, "reference", "team_nicknames.json"), {}) or {}
    write_json(os.path.join(OUT, f"reference-{season}.json"),
               {"fbs": ref, "nicknames": {k: v for k, v in nick.items() if not k.startswith("_")}})


def write_index(season, current, started):
    weeks = []
    for fp in sorted(glob.glob(os.path.join(OUT, "*", "*-w*.json"))):
        d = load_json(fp)
        if not d:
            continue
        weeks.append({"season": d["season"], "week": d["week"], "season_type": d["season_type"],
                      "file": os.path.relpath(fp, OUT).replace(os.sep, "/"), "games": len(d["games"]),
                      "deadline": d.get("deadline"), "frozen": d.get("frozen"), "complete": d.get("complete"),
                      "generated_at": d.get("generated_at")})
    old = load_json(os.path.join(OUT, "index.json"), {}) or {}
    log = (old.get("runs") or [])[-40:]
    log.append({"at": started.isoformat(timespec="minutes"), "cfbd_calls": CALLS["live"], "cached": CALLS["cached"]})
    write_json(os.path.join(OUT, "index.json"), {
        "generated_at": now_utc().isoformat(timespec="minutes"), "season": season,
        "current": current, "weeks": weeks, "runs": log,
        "cfbd_calls_30d": sum(r["cfbd_calls"] for r in log if parse_iso(r["at"]) > now_utc() - timedelta(days=30)),
    })


def parse_list(s):
    out = []
    for part in (s or "").split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            out += list(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", type=int, default=None)
    ap.add_argument("--weeks", default="", help="extra regular-season weeks to (re)build, e.g. 7 or 6,7")
    ap.add_argument("--history", default=None, help="past seasons for the history warehouse (default: last 4)")
    ap.add_argument("--skip-history", action="store_true")
    args = ap.parse_args(argv)
    if not os.getenv("CFBD_API_KEY"):
        sys.exit("CFBD_API_KEY is not set (GitHub: Settings > Secrets and variables > Actions > CFBD_KEY).")
    started = now_utc()
    season = args.season or started.astimezone(TZ).year
    history_seasons = parse_list(args.history) if args.history else list(range(season - 4, season))

    current = cfbd.current_week(season)
    print(f"Season {season}; current week: {current}")
    targets = []
    if current:
        targets.append((current["week"], current["season_type"]))
        if current["week"] > 1:
            targets.append((current["week"] - 1, current["season_type"]))
        targets.append((current["week"] + 1, current["season_type"]))   # next week's card (if CFBD has it yet)
    targets += [(w, "regular") for w in parse_list(args.weeks)]
    for fp in glob.glob(os.path.join(OUT, str(season), "*-w*.json")):   # results still coming in
        d = load_json(fp)
        if d and not d.get("complete"):
            targets.append((d["week"], d["season_type"]))
    targets = sorted(set(targets), key=lambda t: (t[1] != "regular", t[0]))

    if not args.skip_history:
        for w in refresh_history(season, history_seasons):
            print(f"  history warning: {w}")
        msg = maybe_analyze()
        if msg:
            print(f"  {msg}")

    with db.tx() as conn:
        for i, (week, stype) in enumerate(targets):
            build_week(conn, season, week, stype, first_run_extras=(i == 0))
    write_prompts()
    write_reference(season)
    write_index(season, current, started)
    print(f"CFBD calls this run: {CALLS['live']} live, {CALLS['cached']} from cache "
          f"(history: {collect.stats['cfbd_live']} live).")


if __name__ == "__main__":
    main()
