import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

from flask import Flask, jsonify, request, send_from_directory  # noqa: E402

import json  # noqa: E402

import analytics  # noqa: E402
import cfbd  # noqa: E402
import coaches  # noqa: E402
import conditions  # noqa: E402
import fcs  # noqa: E402
import situational  # noqa: E402
from backtest import hfa as hfa_mod  # noqa: E402
import db  # noqa: E402
import history  # noqa: E402
import teams as teams_mod  # noqa: E402
import weather  # noqa: E402
from grading import grade_ats, grade_ou  # noqa: E402

app = Flask(__name__, static_folder="static", static_url_path="")
PROVIDER = os.getenv("LINES_PROVIDER") or None
ANALYTICS_MAX_AGE_HOURS = 12
CALIBRATION_PATH = os.getenv("PICKS_CALIBRATION_PATH",
                             os.path.join(os.path.dirname(__file__), "backtest", "calibration.json"))


HFA_PATH = os.getenv("PICKS_HFA_PATH", os.path.join(os.path.dirname(__file__), "backtest", "hfa.json"))


def load_calibration():
    try:
        with open(CALIBRATION_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None
INJURY_MAX_AGE_HOURS = 12
INTEL_MAX_AGE_HOURS = 24 * 6


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


@app.errorhandler(ApiError)
def _api_error(e):
    return jsonify({"error": str(e)}), e.status


@app.errorhandler(cfbd.CFBDError)
def _cfbd_error(e):
    return jsonify({"error": str(e)}), 502


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ---------- helpers ----------

def _kicked_off(game):
    if not game["start_date"]:
        return bool(game["completed"])
    start = datetime.fromisoformat(game["start_date"].replace("Z", "+00:00"))
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    return game["completed"] or datetime.now(timezone.utc) >= start


def _get_game(conn, game_id):
    game = conn.execute("SELECT * FROM games WHERE id=?", (game_id,)).fetchone()
    if not game:
        raise ApiError("Game not found", 404)
    return game


def _week_args(src):
    try:
        season = int(src.get("season"))
        week = int(src.get("week"))
    except (TypeError, ValueError):
        raise ApiError("season and week are required integers")
    season_type = src.get("season_type") or "regular"
    if season_type not in ("regular", "postseason"):
        raise ApiError("season_type must be 'regular' or 'postseason'")
    return season, week, season_type


def _num_or_none(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        raise ApiError(f"Not a number: {v!r}")


def grade_completed(conn):
    """Grade every pick and prediction on a completed game. Safe to re-run."""
    games = conn.execute(
        "SELECT id, home_points, away_points FROM games WHERE completed=1 AND home_points IS NOT NULL"
    ).fetchall()
    graded = 0
    for g in games:
        hp, ap = g["home_points"], g["away_points"]
        pick = conn.execute("SELECT * FROM picks WHERE game_id=?", (g["id"],)).fetchone()
        if pick:
            conn.execute(
                "UPDATE picks SET ats_result=?, ou_result=?, graded_at=datetime('now') WHERE game_id=?",
                (grade_ats(pick["ats_pick"], pick["spread"], hp, ap),
                 grade_ou(pick["ou_pick"], pick["total"], hp, ap), g["id"]),
            )
            graded += 1
        for p in conn.execute("SELECT * FROM predictions WHERE game_id=?", (g["id"],)).fetchall():
            conn.execute(
                "UPDATE predictions SET ats_result=?, ou_result=? WHERE id=?",
                (grade_ats(p["ats_pick"], p["spread"], hp, ap),
                 grade_ou(p["ou_pick"], p["total"], hp, ap), p["id"]),
            )
    return graded


def _injury_row(conn, game_id):
    row = conn.execute(
        "SELECT *, (julianday('now') - julianday(fetched_at)) * 24 AS age_h FROM injuries WHERE game_id=?",
        (game_id,)).fetchone()
    return db.row_to_dict(row)


def load_hfa():
    try:
        with open(HFA_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _intel_row(conn, team, season, key):
    row = conn.execute("""SELECT *, (julianday('now') - julianday(fetched_at)) * 24 AS age_h FROM intel
                          WHERE team=? AND season=? AND cutoff_key=?""", (team, season, key)).fetchone()
    return (json.loads(row["data"]), row["age_h"]) if row else (None, None)


def save_intel(conn, team, season, key, data):
    conn.execute("""INSERT INTO intel (team, season, cutoff_key, data, fetched_at) VALUES (?, ?, ?, ?, datetime('now'))
                    ON CONFLICT(team, season, cutoff_key) DO UPDATE SET data=excluded.data, fetched_at=excluded.fetched_at""",
                 (team, season, key, json.dumps(data)))


def _needs_fcs(hconn, team, season):
    if teams_mod.program_note(team, season) and "First FBS season" in teams_mod.program_note(team, season):
        return True
    if hconn is None:
        return False
    row = hconn.execute("""SELECT COUNT(*) AS n FROM bt_team_games t JOIN bt_games g ON g.id=t.game_id
                           WHERE t.team=? AND g.season=?""", (team, season - 1)).fetchone()
    return row["n"] == 0


def build_context(conn, game, as_of=None, replay=False, overrides=None, full=False):
    """Everything known about a game beyond the line.

    as_of: information cutoff (default now). replay=True when the cutoff is in the past: strict no-leak mode
    (stats rebuilt from games before the cutoff, opening line, as-issued forecast, dated searches only).
    overrides: cutoff-safe pieces computed by the caller (weather / injuries / intel).
    full: include the heavier pieces (coaching, staff grading, FCS history); the slate list stays light."""
    overrides = overrides or {}
    as_of = as_of or datetime.now(timezone.utc)
    game = dict(game)
    home_t, away_t, season = game["home_team"], game["away_team"], game["season"]
    audit = []
    hconn = history.connect()
    try:
        # Team analytics
        if replay and hconn:
            profs = analytics.asof_profiles(hconn, season, as_of)
            home, away = profs.get(home_t), profs.get(away_t)
            audit.append("Team analytics rebuilt from games played before the cutoff.")
        else:
            home, away = db.get_profile(conn, season, home_t), db.get_profile(conn, season, away_t)

        # Weather
        dome = False
        if game.get("venue_id"):
            v = conn.execute("SELECT dome FROM venues WHERE id=?", (game["venue_id"],)).fetchone()
            dome = bool(v and v["dome"])
        override = None if game["neutral_site"] else teams_mod.home_venue_override(home_t)
        if override and override.get("dome"):
            dome = True
        if "weather" in overrides:
            wx = overrides["weather"]
            audit.append(f"Weather: forecast issued about {wx.get('issued_days_before')} day(s) before kickoff (before the cutoff)."
                         if wx else "Weather: no as-of-cutoff forecast available.")
        elif replay:
            wx = None
            audit.append("Weather: not used (no forecast saved from before the cutoff).")
        else:
            wx = json.loads(game["weather"]) if game.get("weather") else None
        cond = conditions.classify(wx, dome=dome)
        assessment = conditions.assess(home, away, wx, cond, home_t, away_t, load_calibration())

        # Injuries
        if "injuries" in overrides:
            inj = overrides["injuries"]
            audit.append("Injuries: searched with the cutoff; later reports excluded.")
        elif replay:
            inj = None
            audit.append("Injuries: not used (no report from before the cutoff).")
        else:
            row = _injury_row(conn, game["id"])
            inj = {"report": row["report"], "sources": row["sources"] or [], "fetched_at": row["fetched_at"]} if row else None

        hist = situation = None
        coaching, staff_text, intel_data, fcs_hist = {}, {}, {}, {}
        capacity = None
        if hconn:
            hist = history.matchup(hconn, home_t, away_t, season, as_of=as_of if replay else None)
            situation = situational.game_situation(hconn, game, as_of=as_of)
            if game.get("venue_id"):
                vr = hconn.execute("SELECT capacity FROM bt_venues WHERE id=?", (game["venue_id"],)).fetchone()
                capacity = vr["capacity"] if vr else None
            if replay:
                audit.append("Betting history, situation and coach records use only games before the cutoff.")
        hfa_text = hfa_mod.describe(load_hfa(), home_t, away_t, bool(game["neutral_site"]), wx, capacity) if load_hfa() else None

        if full:
            for team in (away_t, home_t):
                if hconn:
                    coaching[team] = coaches.profile(hconn, team, season, as_of if replay else None)
                data = overrides.get("intel", {}).get(team) if "intel" in overrides else (
                    None if replay else _intel_row(conn, team, season, "live")[0])
                if data:
                    intel_data[team] = data
                    if hconn:
                        cache = {}
                        graded = [{**m, "graded": coaches.grade_staff_member(hconn, m, season - 1, cache)}
                                  for m in (data.get("staff") or [])]
                        staff_text[team] = coaches.describe_staff(graded)
                if _needs_fcs(hconn, team, season):
                    try:
                        fcs_hist[team] = fcs.history(team, season, years=2)
                    except Exception as e:
                        fcs_hist[team] = {"error": str(e)}
            if replay and "intel" in overrides:
                audit.append("Program intel searched with the cutoff; later items excluded.")
    finally:
        if hconn:
            hconn.close()
    if replay:
        audit.append("Home field and weather calibration use completed seasons only.")
    return {
        "as_of": as_of.isoformat(timespec="minutes"),
        "replay": replay,
        "audit": audit,
        "history": hist,
        "situation": situation,
        "hfa_text": hfa_text,
        "coaching": coaching,
        "intel": intel_data,
        "staff_text": staff_text,
        "fcs": fcs_hist,
        "home_profile": home,
        "away_profile": away,
        "program_notes": {t: n for t in (away_t, home_t) if (n := teams_mod.program_note(t, season))},
        "assessment": assessment,
        "injuries": inj,
    }


def refresh_week_extras(conn, season, games, force_analytics=False):
    """Analytics, venues and weather for a synced week. Failures become warnings, not errors."""
    warnings = []
    if (db.teams_age_hours(conn, season) or 999) > 24:
        try:
            fbs = teams_mod.fetch_fbs_teams(season)
            db.save_teams(conn, season, fbs)
            check = teams_mod.verify(fbs, teams_mod.load_reference(season))
            if check.get("checked") and not check["ok"]:
                warnings.append("Team list check: " + check["message"])
        except Exception as e:
            warnings.append(f"FBS team list not updated: {e}")
    age = db.profiles_age_hours(conn, season)
    if force_analytics or age is None or age > ANALYTICS_MAX_AGE_HOURS:
        try:
            db.save_profiles(conn, season, analytics.fetch_profiles(season))
        except Exception as e:
            warnings.append(f"Analytics not updated: {e}")

    try:  # keep the historical lines/stats warehouse current (cached; a few calls per sync)
        from backtest import collect as bt_collect
        hconn = bt_collect.connect(history.DB_PATH)
        bt_collect.collect_season(hconn, season)
        warnings += bt_collect.collect_context(hconn, season)
        if not hconn.execute("SELECT 1 FROM bt_venues LIMIT 1").fetchone():
            bt_collect.collect_venues(hconn)
        hconn.close()
    except Exception as e:
        warnings.append(f"History not updated: {e}")

    if not conn.execute("SELECT 1 FROM venues LIMIT 1").fetchone():
        try:
            db.save_venues(conn, weather.fetch_venues())
        except Exception as e:
            warnings.append(f"Venues not loaded: {e}")

    forecasts = 0
    for g in games:
        if g["completed"] or not g.get("venue_id"):
            continue
        v = conn.execute("SELECT * FROM venues WHERE id=?", (g["venue_id"],)).fetchone()
        if not v or v["dome"]:
            continue
        try:
            wx = weather.fetch_forecast(v["latitude"], v["longitude"], g["start_date"])
        except Exception as e:
            warnings.append(f"Weather failed for {g['away_team']} at {g['home_team']}: {e}")
            continue
        if wx:
            conn.execute("UPDATE games SET weather=?, weather_updated_at=datetime('now') WHERE id=?",
                         (json.dumps(wx), g["id"]))
            forecasts += 1
    return forecasts, warnings[:5]


def _history_summary(h):
    if not h:
        return None
    def side(t):
        return {"team": t["team"], "season": {k: t["season"][k] for k in ("all", "home", "away", "favorite", "underdog", "last10")},
                "five_years": {k: t["five_years"][k] for k in ("all", "home", "away", "favorite", "underdog")},
                "stats": {"for": t["stats"]["for"], "against": t["stats"]["against"]}}
    return {"home": side(h["teams"]["home"]), "away": side(h["teams"]["away"]), "h2h": h["h2h"][:6]}


def _profile_summary(p):
    if not p:
        return None
    return {"style": p["style"], "pct": p["pct"], "pass_rate": p["off"]["pass_rate"],
            "plays_per_game": p["plays_per_game"], "sp": p["sp"]}


# ---------- routes ----------

@app.get("/api/teams")
def list_teams():
    season = int(request.args.get("season") or datetime.now().year)
    with db.tx() as conn:
        fbs = db.get_teams(conn, season)
    ref = teams_mod.load_reference(season)
    if not fbs and ref:  # not synced yet: show the verified list
        fbs = [{"school": t, "conference": c} for c, ts in ref["conferences"].items() for t in ts]
        source = "reference"
    else:
        source = "cfbd"
    notes = (ref or {}).get("program_notes", {})
    out = {c: [{**t, "note": notes.get(teams_mod.canonical(t["school"], ref))} for t in ts]
           for c, ts in teams_mod.by_conference(fbs, ref).items()}
    return jsonify({"season": season, "source": source, "count": len(fbs),
                    "check": teams_mod.verify(fbs, ref) if source == "cfbd" else
                    {"checked": False, "message": f"Showing the verified {season} list ({len(fbs)} teams). Sync to compare with CFBD."},
                    "conferences": out, "sources": (ref or {}).get("sources", [])})

@app.get("/api/current-week")
def current_week():
    season = int(request.args.get("season") or datetime.now().year)
    try:
        found = cfbd.current_week(season)
    except cfbd.CFBDError as e:
        return jsonify({"season": season, "week": None, "season_type": "regular", "warning": str(e)})
    return jsonify({"season": season, **(found or {"week": None, "season_type": "regular"})})


@app.get("/api/games")
def list_games():
    season, week, season_type = _week_args(request.args)
    with db.tx() as conn:
        games = conn.execute(
            "SELECT * FROM games WHERE season=? AND week=? AND season_type=? ORDER BY start_date, id",
            (season, week, season_type),
        ).fetchall()
        out = []
        for g in games:
            d = dict(g)
            d["kicked_off"] = bool(_kicked_off(g))
            d["pick"] = db.row_to_dict(conn.execute("SELECT * FROM picks WHERE game_id=?", (g["id"],)).fetchone())
            locked = None
            if d["pick"] and d["pick"]["prediction_id"]:
                locked = conn.execute("SELECT * FROM predictions WHERE id=?", (d["pick"]["prediction_id"],)).fetchone()
            latest = locked or conn.execute(
                "SELECT * FROM predictions WHERE game_id=? ORDER BY id DESC LIMIT 1", (g["id"],)
            ).fetchone()
            d["prediction"] = db.row_to_dict(latest)
            if d["prediction"]:
                d["prediction"].pop("context", None)  # large; the live context is below
            d["weather"] = json.loads(g["weather"]) if g["weather"] else None
            ctx = build_context(conn, g)
            d["assessment"] = ctx["assessment"]
            d["injuries"] = ctx["injuries"]
            d["profiles"] = {"home": _profile_summary(ctx["home_profile"]), "away": _profile_summary(ctx["away_profile"])}
            d["history"] = _history_summary(ctx["history"])
            out.append(d)
    return jsonify(out)


@app.post("/api/sync")
def sync():
    body = request.get_json(force=True) or {}
    season, week, season_type = _week_args(body)
    games = cfbd.fetch_games(season, week, season_type)
    lines = cfbd.fetch_lines(season, week, season_type, PROVIDER)
    with db.tx() as conn:
        for g in games:
            db.upsert_game(conn, g)
            if g["id"] in lines:
                db.update_line(conn, g["id"], lines[g["id"]])
        graded = grade_completed(conn)
    with db.tx() as conn:
        forecasts, warnings = refresh_week_extras(conn, season, games, bool(body.get("refresh_analytics")))
    return jsonify({"games": len(games), "with_lines": sum(1 for g in games if g["id"] in lines),
                    "graded": graded, "forecasts": forecasts, "warnings": warnings})


@app.post("/api/grade")
def grade():
    season, week, season_type = _week_args(request.get_json(force=True) or {})
    games = cfbd.fetch_games(season, week, season_type)
    with db.tx() as conn:
        for g in games:
            db.upsert_game(conn, g)
        graded = grade_completed(conn)
    return jsonify({"completed": sum(g["completed"] for g in games), "graded": graded})


@app.post("/api/games/<int:game_id>/notes")
def save_notes(game_id):
    notes = (request.get_json(force=True) or {}).get("notes", "")
    with db.tx() as conn:
        _get_game(conn, game_id)
        conn.execute("UPDATE games SET notes=? WHERE id=?", (notes, game_id))
    return jsonify({"ok": True})


def _fetch_injuries(game_id):
    from injuries import fetch_report

    with db.tx() as conn:
        game = dict(_get_game(conn, game_id))
    try:
        found = fetch_report(game)
    except Exception as e:
        raise ApiError(f"Injury search failed: {e}", 502)
    with db.tx() as conn:
        conn.execute(
            """INSERT INTO injuries (game_id, report, sources, fetched_at) VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT(game_id) DO UPDATE SET report=excluded.report, sources=excluded.sources,
                   fetched_at=excluded.fetched_at""",
            (game_id, found["report"], json.dumps(found["sources"])),
        )
        return _injury_row(conn, game_id)


def refresh_intel(team, season, force=False):
    """Weekly program-intel search for a team (staff, hot seat, rumors, NIL)."""
    from intel import fetch as fetch_intel
    with db.tx() as conn:
        data, age = _intel_row(conn, team, season, "live")
    if data and not force and age is not None and age < INTEL_MAX_AGE_HOURS:
        return data
    try:
        data = fetch_intel(team, season)
    except Exception as e:
        raise ApiError(f"Program intel search failed for {team}: {e}", 502)
    with db.tx() as conn:
        save_intel(conn, team, season, "live", data)
    return data


@app.post("/api/intel/refresh")
def api_refresh_intel():
    body = request.get_json(force=True) or {}
    if not body.get("team") or not body.get("season"):
        raise ApiError("team and season are required")
    return jsonify(refresh_intel(body["team"], int(body["season"]), force=True))


@app.get("/api/games/<int:game_id>/context")
def game_context(game_id):
    """Full context for a game card (coaching, staff, intel, FCS history, situation, home field)."""
    with db.tx() as conn:
        game = _get_game(conn, game_id)
        ctx = build_context(conn, game, full=True)
    return jsonify({k: ctx[k] for k in ("situation", "hfa_text", "coaching", "intel", "staff_text", "fcs", "program_notes")} |
                   {"situation_flags": situational.flags(ctx["situation"]),
                    "coaching_text": {t: coaches.describe(p) for t, p in ctx["coaching"].items()},
                    "fcs_text": {t: fcs.describe(t, h) for t, h in ctx["fcs"].items() if "error" not in h}})


@app.post("/api/games/<int:game_id>/injuries")
def refresh_injuries(game_id):
    return jsonify(_fetch_injuries(game_id))


@app.post("/api/games/<int:game_id>/predict")
def make_prediction(game_id):
    from predict import predict  # imported lazily so the app runs without an API key

    body = request.get_json(force=True) or {}
    with db.tx() as conn:
        game = _get_game(conn, game_id)
        if conn.execute("SELECT 1 FROM picks WHERE game_id=?", (game_id,)).fetchone():
            raise ApiError("Your pick is saved, so Claude's pick is locked.")
        if "notes" in body:
            conn.execute("UPDATE games SET notes=? WHERE id=?", (body["notes"], game_id))
        if game["spread"] is None and game["total"] is None:
            raise ApiError("No line for this game yet. Sync the slate first.")
        inj = _injury_row(conn, game_id)

    if body.get("search_injuries") and (not inj or inj["age_h"] > INJURY_MAX_AGE_HOURS):
        _fetch_injuries(game_id)
    if body.get("search_intel"):
        with db.tx() as conn:
            g = _get_game(conn, game_id)
        for team in (g["away_team"], g["home_team"]):
            refresh_intel(team, g["season"])

    with db.tx() as conn:
        game = _get_game(conn, game_id)
        context = build_context(conn, game, full=True)
        context["lessons"] = pool_api.lessons_for(conn, game["season"], game["week"], game["season_type"])
        game = dict(game)

    try:
        row = predict(game, game["notes"], context)
    except Exception as e:  # surface API / parsing errors to the UI
        raise ApiError(f"Prediction failed: {e}", 502)

    return jsonify(_save_prediction(row))


def _save_prediction(row):
    with db.tx() as conn:
        cur = conn.execute(
            """INSERT INTO predictions (game_id, model, spread, total, notes, ats_pick, ou_pick,
                   proj_home, proj_away, confidence, reasoning, key_factors, conditions_impact, context)
               VALUES (:game_id, :model, :spread, :total, :notes, :ats_pick, :ou_pick,
                   :proj_home, :proj_away, :confidence, :reasoning, :key_factors, :conditions_impact, :context)""",
            row,
        )
        saved = db.row_to_dict(conn.execute("SELECT * FROM predictions WHERE id=?", (cur.lastrowid,)).fetchone())
    saved.pop("context", None)
    return saved


def _manual_game(game_id, notes=None):
    with db.tx() as conn:
        game = _get_game(conn, game_id)
        if conn.execute("SELECT 1 FROM picks WHERE game_id=?", (game_id,)).fetchone():
            raise ApiError("Your pick is saved, so Claude's pick is locked.")
        if _kicked_off(game):
            raise ApiError("This game has already kicked off.")
        if notes is not None:
            conn.execute("UPDATE games SET notes=? WHERE id=?", (notes, game_id))
            game = _get_game(conn, game_id)
        if game["spread"] is None and game["total"] is None:
            raise ApiError("No line for this game yet. Sync the slate first.")
        return dict(game)


@app.get("/api/config")
def api_config():
    import manual
    return jsonify({"claude_mode": manual.mode()})


@app.post("/api/games/<int:game_id>/prompt")
def game_prompt(game_id):
    """Copy-for-Claude: the full prompt for one slate game."""
    import manual
    game = _manual_game(game_id, (request.get_json(force=True) or {}).get("notes"))
    with db.tx() as conn:
        context = build_context(conn, game, full=True)
        context["lessons"] = pool_api.lessons_for(conn, game["season"], game["week"], game["season_type"])
    return jsonify({"prompt": manual.game_prompt(game, game["notes"], context)})


@app.post("/api/games/<int:game_id>/answer")
def game_answer(game_id):
    """Copy-for-Claude: save the pasted answer for one slate game."""
    import manual
    from predict import row_from_data
    game = _manual_game(game_id)
    try:
        answers = manual.answers_by_item(manual.extract_json((request.get_json(force=True) or {}).get("answer")))
        row = row_from_data(game, next(iter(answers.values())), game["notes"], None, manual.MANUAL_MODEL)
    except (ValueError, TypeError) as e:
        raise ApiError(str(e))
    return jsonify(_save_prediction(row))


@app.post("/api/games/<int:game_id>/pick")
def save_pick(game_id):
    body = request.get_json(force=True) or {}
    ats, ou = body.get("ats_pick") or None, body.get("ou_pick") or None
    if ats not in (None, "home", "away") or ou not in (None, "over", "under"):
        raise ApiError("Invalid pick")
    if not ats and not ou:
        raise ApiError("Pick a side, a total, or both.")
    spread, total = _num_or_none(body.get("spread")), _num_or_none(body.get("total"))
    if ats and spread is None:
        raise ApiError("A spread is required for an ATS pick.")
    if ou and total is None:
        raise ApiError("A total is required for an O/U pick.")

    with db.tx() as conn:
        game = _get_game(conn, game_id)
        if _kicked_off(game):
            raise ApiError("This game has already kicked off.")
        if conn.execute("SELECT 1 FROM picks WHERE game_id=?", (game_id,)).fetchone():
            raise ApiError("You already have a pick on this game. Delete it first to change it.")
        pred = conn.execute(
            "SELECT id FROM predictions WHERE game_id=? ORDER BY id DESC LIMIT 1", (game_id,)
        ).fetchone()
        pred_id = pred["id"] if pred else None
        conn.execute(
            """INSERT INTO picks (game_id, prediction_id, ats_pick, ou_pick, spread, total)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (game_id, pred_id, ats, ou, spread if ats else None, total if ou else None),
        )
        if pred_id:
            conn.execute("UPDATE predictions SET locked=1 WHERE id=?", (pred_id,))
    return jsonify({"ok": True, "locked_prediction_id": pred_id})


@app.delete("/api/games/<int:game_id>/pick")
def delete_pick(game_id):
    with db.tx() as conn:
        game = _get_game(conn, game_id)
        if _kicked_off(game):
            raise ApiError("Picks can't be removed after kickoff.")
        pick = conn.execute("SELECT * FROM picks WHERE game_id=?", (game_id,)).fetchone()
        if not pick:
            raise ApiError("No pick to delete", 404)
        conn.execute("DELETE FROM picks WHERE game_id=?", (game_id,))
        if pick["prediction_id"]:
            conn.execute("UPDATE predictions SET locked=0 WHERE id=?", (pick["prediction_id"],))
    return jsonify({"ok": True})


def _tally(results):
    t = {"W": 0, "L": 0, "P": 0}
    for r in results:
        if r in t:
            t[r] += 1
    decided = t["W"] + t["L"]
    t["pct"] = round(100 * t["W"] / decided, 1) if decided else None
    return t


@app.get("/api/record")
def record():
    season = request.args.get("season", type=int)
    where, params = ("WHERE g.season=?", [season]) if season else ("", [])
    with db.tx() as conn:
        picks = conn.execute(
            f"""SELECT p.*, g.season, g.week, g.season_type, g.home_team, g.away_team, g.start_date,
                       g.home_points, g.away_points, g.completed,
                       pr.ats_pick AS c_ats, pr.ou_pick AS c_ou, pr.spread AS c_spread, pr.total AS c_total,
                       pr.confidence AS c_conf, pr.ats_result AS c_ats_result, pr.ou_result AS c_ou_result
                FROM picks p JOIN games g ON g.id=p.game_id
                LEFT JOIN predictions pr ON pr.id=p.prediction_id
                {where} ORDER BY g.start_date DESC""",
            params,
        ).fetchall()
        # Claude's "official" pick per game: the locked one if I picked, else its latest prediction.
        claude = conn.execute(
            f"""SELECT pr.* FROM predictions pr JOIN games g ON g.id=pr.game_id
                {where + (' AND' if where else 'WHERE')} g.completed=1 AND pr.id = COALESCE(
                    (SELECT prediction_id FROM picks WHERE game_id=pr.game_id),
                    (SELECT MAX(id) FROM predictions WHERE game_id=pr.game_id))""",
            params,
        ).fetchall()

    picks = [dict(p) for p in picks]
    locked = [p for p in picks if p["prediction_id"]]

    def split(key, claude_key):
        agree = [p for p in locked if p[key] and p[key] == p[claude_key]]
        fade = [p for p in locked if p[key] and p[claude_key] and p[key] != p[claude_key]]
        return agree, fade

    ats_agree, ats_fade = split("ats_pick", "c_ats")
    ou_agree, ou_fade = split("ou_pick", "c_ou")

    buckets = []
    for lo, hi in ((50, 59), (60, 69), (70, 79), (80, 95)):
        rows = [c for c in claude if c["confidence"] is not None and lo <= c["confidence"] <= hi]
        buckets.append({
            "range": f"{lo}-{hi}",
            "ats": _tally(c["ats_result"] for c in rows),
            "ou": _tally(c["ou_result"] for c in rows),
        })

    return jsonify({
        "me": {"ats": _tally(p["ats_result"] for p in picks), "ou": _tally(p["ou_result"] for p in picks)},
        "claude_locked": {"ats": _tally(p["c_ats_result"] for p in locked),
                          "ou": _tally(p["c_ou_result"] for p in locked)},
        "claude_all": {"ats": _tally(c["ats_result"] for c in claude),
                       "ou": _tally(c["ou_result"] for c in claude)},
        "agree": {"ats": _tally(p["ats_result"] for p in ats_agree), "ou": _tally(p["ou_result"] for p in ou_agree)},
        "fade": {"ats": _tally(p["ats_result"] for p in ats_fade), "ou": _tally(p["ou_result"] for p in ou_fade)},
        "confidence": buckets,
        "history": picks,
    })


import pool_api  # noqa: E402

pool_api.init(build_context=build_context, kicked_off=_kicked_off, ApiError=ApiError,
              intel_row=_intel_row, save_intel=save_intel)
app.register_blueprint(pool_api.bp)

import league_api  # noqa: E402

league_api.init(card_payload=pool_api._card_payload, ApiError=ApiError)
app.register_blueprint(league_api.bp)
db.init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", 5050)), debug=os.getenv("FLASK_DEBUG") == "1")
