"""League routes: paste each member's picks, score them, leaderboard, infer unknown rules."""
import json

from flask import Blueprint, Response, jsonify, request

import db
import league
import pool
import teams as teams_mod

bp = Blueprint("league", __name__)
deps = {}  # card_payload, ApiError, card (lookup) injected by app.py


def init(**kwargs):
    deps.update(kwargs)


def _err(msg, status=400):
    return deps["ApiError"](msg, status)


def get_scoring(conn):
    row = conn.execute("SELECT value FROM settings WHERE key='scoring'").fetchone()
    return league.merged(json.loads(row["value"]) if row else None)


def _card(conn, src):
    try:
        season, week = int(src.get("season")), int(src.get("week"))
    except (TypeError, ValueError):
        raise _err("season and week are required")
    card = conn.execute("SELECT * FROM pool_cards WHERE season=? AND week=? AND season_type=?",
                        (season, week, src.get("season_type") or "regular")).fetchone()
    if not card:
        raise _err("No card for this week yet. Import the week's card on the Pool tab first.")
    return card


def _entries(conn, card_id):
    out = []
    for r in conn.execute("""SELECT e.*, p.name FROM entries e JOIN players p ON p.id=e.player_id
                             WHERE e.card_id=? ORDER BY p.name""", (card_id,)):
        out.append({"id": r["id"], "player": r["name"], "picks": json.loads(r["picks"]), "tiebreaker": r["tiebreaker"],
                    "reported_points": r["reported_points"], "warnings": json.loads(r["warnings"] or "[]")})
    return out


def _claude_entry(items):
    picks, proj = {}, 0
    for it in items:
        c = it.get("claude")
        if not c:
            continue
        if it["kind"] == "prop":
            picks[str(it["idx"])] = {"pick": c.get("pick")}
        else:
            picks[str(it["idx"])] = {"ats": c.get("ats_pick"), "ou": c.get("ou_pick")}
            if it["kind"] == "game" and c.get("proj_home") is not None:
                proj += c["proj_home"] + c["proj_away"]
    return {"player": "Claude (not in league)", "picks": picks, "tiebreaker": round(proj) if proj else None} if picks else None


def _week(conn, card, cfg):
    items = deps["card_payload"](conn, card)["items"]
    entries = _entries(conn, card["id"])
    scored = league.score_week(items, entries, cfg)
    ce = _claude_entry(items)
    if ce:   # scored on its own so it never takes Player of the Week from a real member
        alone = league.score_week(items, [ce], {**cfg, "potw_bonus": 0})["rows"][0]
        alone["tiebreak_points"] = league.tiebreak_points(cfg["tiebreak"], {**{r["player"]: r["tiebreaker"] for r in scored["rows"]},
                                                                            ce["player"]: ce["tiebreaker"]},
                                                          scored["actual_combined"]).get(ce["player"], 0)
        alone["potw"], alone["potw_points"] = False, 0
        alone["total"] = alone["base"] + alone["tiebreak_points"]
        scored["claude"] = alone
    return items, entries, scored


@bp.get("/api/league/scoring")
def scoring():
    with db.tx() as conn:
        return jsonify(get_scoring(conn))


@bp.post("/api/league/scoring")
def save_scoring():
    body = request.get_json(force=True) or {}
    cfg = league.merged(body)
    with db.tx() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('scoring', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (json.dumps(cfg),))
    return jsonify(cfg)


@bp.post("/api/league/entries")
def add_entry():
    body = request.get_json(force=True) or {}
    name, text = (body.get("name") or "").strip(), (body.get("text") or "").strip()
    if not name or not text:
        raise _err("Enter the player's name and paste their picks.")
    with db.tx() as conn:
        card = _card(conn, body)
        items = deps["card_payload"](conn, card)["items"]
        parsed = pool.parse_entry(text, items, teams_mod.load_reference(card["season"]))
        if not parsed["picks"]:
            raise _err("Couldn't find any picks in that text.")
        conn.execute("INSERT OR IGNORE INTO players (name) VALUES (?)", (name,))
        pid = conn.execute("SELECT id FROM players WHERE name=?", (name,)).fetchone()["id"]
        rp = body.get("reported_points")
        conn.execute("""INSERT INTO entries (card_id, player_id, raw, picks, tiebreaker, reported_points, warnings)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(card_id, player_id) DO UPDATE SET raw=excluded.raw, picks=excluded.picks,
                            tiebreaker=excluded.tiebreaker, reported_points=COALESCE(excluded.reported_points, entries.reported_points),
                            warnings=excluded.warnings""",
                     (card["id"], pid, text, json.dumps(parsed["picks"]), parsed["tiebreaker"],
                      float(rp) if rp not in (None, "") else None, json.dumps(parsed["warnings"])))
    return jsonify({"ok": True, "player": name, "picks": len(parsed["picks"]), "tiebreaker": parsed["tiebreaker"],
                    "warnings": parsed["warnings"]})


@bp.post("/api/league/entries/<int:entry_id>")
def edit_entry(entry_id):
    body = request.get_json(force=True) or {}
    with db.tx() as conn:
        if "reported_points" in body:
            v = body["reported_points"]
            conn.execute("UPDATE entries SET reported_points=? WHERE id=?", (float(v) if v not in (None, "") else None, entry_id))
        if "tiebreaker" in body:
            v = body["tiebreaker"]
            conn.execute("UPDATE entries SET tiebreaker=? WHERE id=?", (int(v) if v not in (None, "") else None, entry_id))
    return jsonify({"ok": True})


@bp.delete("/api/league/entries/<int:entry_id>")
def delete_entry(entry_id):
    with db.tx() as conn:
        conn.execute("DELETE FROM entries WHERE id=?", (entry_id,))
    return jsonify({"ok": True})


@bp.get("/api/league/week")
def week_view():
    with db.tx() as conn:
        card = _card(conn, request.args)
        cfg = get_scoring(conn)
        items, entries, scored = _week(conn, card, cfg)
    by_player = {e["player"]: e for e in entries}
    for r in scored["rows"]:
        e = by_player.get(r["player"], {})
        r["entry_id"], r["reported_points"], r["warnings"] = e.get("id"), e.get("reported_points"), e.get("warnings", [])
    labels = [{"idx": it["idx"], "kind": it["kind"],
               "label": (f"Q{it['data'].get('n')}" if it["kind"] == "prop" else f"{it['data']['away']} @ {it['data']['home']}"),
               "home": it["data"].get("home"), "away": it["data"].get("away"), "weight": it["data"].get("weight", 1)}
              for it in items]
    return jsonify({"card_id": card["id"], "week": card["week"], "items": labels, "scoring": cfg, **scored})


@bp.get("/api/league/leaderboard")
def leaderboard_view():
    season = request.args.get("season", type=int)
    with db.tx() as conn:
        cfg = get_scoring(conn)
        weeks = []
        claude_total = 0
        for card in conn.execute("SELECT * FROM pool_cards WHERE season=? ORDER BY season_type, week", (season,)).fetchall():
            _, entries, scored = _week(conn, card, cfg)
            if entries:
                weeks.append({"week": card["week"], "season_type": card["season_type"], "complete": scored["complete"],
                              "rows": scored["rows"], "actual_combined": scored["actual_combined"],
                              "potw": [r["player"] for r in scored["rows"] if r["potw"]]})
            if scored.get("claude"):
                claude_total += scored["claude"]["total"]
    return jsonify({"season": season, "scoring": cfg, "standings": league.leaderboard(weeks),
                    "weeks": [{k: w[k] for k in ("week", "season_type", "complete", "actual_combined", "potw")} for w in weeks],
                    "claude_total": claude_total})


@bp.get("/api/league/infer")
def infer_view():
    season = request.args.get("season", type=int)
    with db.tx() as conn:
        cfg = get_scoring(conn)
        weeks = []
        for card in conn.execute("SELECT * FROM pool_cards WHERE season=?", (season,)).fetchall():
            items = deps["card_payload"](conn, card)["items"]
            weeks.append({"week": card["week"], "items": items, "entries": _entries(conn, card["id"])})
    return jsonify(league.infer_rules(weeks, cfg))


def get_highlights(conn):
    row = conn.execute("SELECT value FROM settings WHERE key='highlights'").fetchone()
    return json.loads(row["value"]) if row else dict(league.DEFAULT_HIGHLIGHTS)


@bp.get("/api/league/highlights")
def highlights_view():
    with db.tx() as conn:
        return jsonify(get_highlights(conn))


@bp.post("/api/league/highlights")
def save_highlights():
    body = request.get_json(force=True) or {}
    clean = {str(k).strip(): v for k, v in body.items() if str(k).strip() and v in ("red", "gold", "green", "blue")}
    with db.tx() as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('highlights', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (json.dumps(clean),))
    return jsonify(clean)


def _csv_response(text, filename):
    return Response(text, mimetype="text/csv", headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@bp.get("/api/league/sheet/week")
def week_sheet_view():
    with db.tx() as conn:
        card = _card(conn, request.args)
        cfg = get_scoring(conn)
        items, entries, scored = _week(conn, card, cfg)
        sheet = league.week_sheet(items, scored, entries, get_highlights(conn))
    if request.args.get("format") == "csv":
        header = ["Rank", "Player"] + [f"{c['label']} ({c['line']})" + (f" [{c['weight']} pts]" if c["weight"] > 1 else "")
                                       for c in sheet["columns"]] + \
                 ["Spread pts", "O/U pts", "Bonus game pts", "Question pts", "Mid-major pts", "Combined guess", "Off by",
                  "Combined pts", "POTW", "Total"]
        out = []
        for i, r in enumerate(sheet["rows"], start=1):
            b = r["breakdown"]
            cells = [f"{c['label']} ({'/'.join(x for x in (c['ats_result'], c['ou_result']) if x) or '-'})" if c["label"] else ""
                     for c in r["cells"]]
            out.append([i, r["player"], *cells, b["ats"], b["ou"], b["bonus_game"], b["prop"], b["mid_major"],
                        r["tiebreaker"], r["distance"], r["tiebreak_points"], "YES" if r["potw"] else "", r["total"]])
        return _csv_response(league.to_csv(header, out), f"week{card['week']}_{card['season']}.csv")
    return jsonify({"week": card["week"], "season": card["season"], "actual_combined": scored["actual_combined"],
                    "complete": scored["complete"], **sheet})


@bp.get("/api/league/sheet/season")
def season_sheet_view():
    season = request.args.get("season", type=int)
    with db.tx() as conn:
        cfg = get_scoring(conn)
        weeks = []
        for card in conn.execute("SELECT * FROM pool_cards WHERE season=? ORDER BY season_type, week", (season,)).fetchall():
            _, entries, scored = _week(conn, card, cfg)
            if entries:
                weeks.append({"week": card["week"], "rows": scored["rows"]})
        sheet = league.season_sheet(league.leaderboard(weeks), weeks, get_highlights(conn))
    if request.args.get("format") == "csv":
        header = ["Rank", "Player"] + [f"Week {w}" for w in sheet["weeks"]] + ["Total", "Behind", "POTW", "Pick %"]
        out = [[r["rank"], r["player"], *[("" if v is None else v) for v in r["weeks_cells"]], r["total"], r["behind"],
                r["potw"], r["pick_pct"]] for r in sheet["rows"]]
        return _csv_response(league.to_csv(header, out), f"season_{season}.csv")
    return jsonify({"season": season, **sheet})
