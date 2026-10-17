"""Pool card routes: import the weekly email, predict against the pool's lines, pick, export, grade."""
import json
from datetime import datetime, time, timedelta, timezone

from flask import Blueprint, jsonify, request

import cfbd
import db
import pool
import teams as teams_mod

bp = Blueprint("pool", __name__)
deps = {}  # build_context, kicked_off, ApiError: injected by app.py (avoids a circular import)


def init(**kwargs):
    deps.update(kwargs)


def _err(msg, status=400):
    return deps["ApiError"](msg, status)


def _week_games(conn, season, week, season_type):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM games WHERE season=? AND week=? AND season_type=?", (season, week, season_type))]


def _card(conn, season, week, season_type):
    return conn.execute("SELECT * FROM pool_cards WHERE season=? AND week=? AND season_type=?",
                        (season, week, season_type)).fetchone()


def _items(conn, card_id):
    out = []
    for r in conn.execute("SELECT * FROM pool_items WHERE card_id=? ORDER BY idx", (card_id,)):
        d = dict(r)
        d["data"] = json.loads(d["data"])
        d["claude"] = json.loads(d["claude"]) if d["claude"] else None
        out.append(d)
    return out


def _now():
    return datetime.now(timezone.utc)


def _parse_iso(s):
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.astimezone()


def default_deadline(games):
    """Friday 12:00 PM (local time) before the week's main Saturday."""
    sats = []
    for g in games:
        if g.get("start_date"):
            local = _parse_iso(g["start_date"]).astimezone()
            sats.append((local + timedelta(days=(5 - local.weekday()) % 7)).date())
    if not sats:
        return None
    sat = max(set(sats), key=sats.count)
    friday_noon = datetime.combine(sat - timedelta(days=1), time(12, 0)).astimezone()
    return friday_noon.astimezone(timezone.utc).isoformat(timespec="minutes")


def past_deadline(card):
    return bool(card["deadline"]) and _now() >= _parse_iso(card["deadline"])


def _locked(card, game):
    return past_deadline(card) or (bool(deps["kicked_off"](game)) if game else False)


def _card_for_item(conn, item_id):
    it = conn.execute("SELECT * FROM pool_items WHERE id=?", (item_id,)).fetchone()
    if not it:
        raise _err("Item not found", 404)
    card = conn.execute("SELECT * FROM pool_cards WHERE id=?", (it["card_id"],)).fetchone()
    return it, card


def _pool_game(game, item):
    """CFBD game dict with the POOL's line, in CFBD home perspective."""
    g = dict(game)
    spread = item["home_spread"]
    g["spread"] = -spread if item.get("flipped") else spread
    g["total"] = item.get("total")
    g["spread_open"] = g["total_open"] = None
    return g


@bp.post("/api/pool/import")
def import_card():
    body = request.get_json(force=True) or {}
    try:
        season, week = int(body["season"]), int(body["week"])
    except (KeyError, TypeError, ValueError):
        raise _err("season and week are required")
    season_type = body.get("season_type") or "regular"
    text = (body.get("text") or "").strip()
    if not text:
        raise _err("Paste the pool card text.")
    parsed = pool.parse_card(text)
    if not parsed["games"]:
        raise _err("No games found. Lines should look like: Ohio State @ Indiana (+3.5) o/u 50.5")
    ref = teams_mod.load_reference(season)
    with db.tx() as conn:
        week_games = _week_games(conn, season, week, season_type)
    if not week_games:   # e.g. importing a past week: fetch that week's games from CFBD
        try:
            fetched = cfbd.fetch_games(season, week, season_type)
        except Exception as e:
            raise _err(f"No games stored for this week and CFBD fetch failed ({e}). Sync the week first.")
        with db.tx() as conn:
            for g in fetched:
                db.upsert_game(conn, g)
    with db.tx() as conn:
        week_games = _week_games(conn, season, week, season_type)
        if not week_games:
            raise _err("No games found for this week.")
        pool.match_games(parsed["games"], week_games, ref)
        props = [pool.interpret_prop(p, week_games, ref) for p in parsed["props"]]
        old = _card(conn, season, week, season_type)
        matched = [g for g in week_games if g["id"] in {cg["game_id"] for cg in parsed["games"]}]
        deadline = (old["deadline"] if old and old["deadline"] else None) or default_deadline(matched or week_games)
        if old:
            if past_deadline(old):
                raise _err("This week's picks are already locked (deadline passed).")
            conn.execute("DELETE FROM pool_items WHERE card_id=?", (old["id"],))
            conn.execute("DELETE FROM pool_cards WHERE id=?", (old["id"],))
        cur = conn.execute(
            "INSERT INTO pool_cards (season, week, season_type, raw, has_tiebreaker, tiebreaker, deadline) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (season, week, season_type, text, 1 if parsed["has_tiebreaker"] else 0, parsed["tiebreaker"], deadline))
        card_id = cur.lastrowid
        idx = 0
        for g in parsed["games"]:
            g["line_entered_at"] = _now().isoformat(timespec="minutes")
            conn.execute("INSERT INTO pool_items (card_id, idx, kind, data, my_ats, my_ou) VALUES (?, ?, ?, ?, ?, ?)",
                         (card_id, idx, g["kind"], json.dumps(g), g.get("my_ats"), g.get("my_ou")))
            idx += 1
        for p in props:
            conn.execute("INSERT INTO pool_items (card_id, idx, kind, data, my_prop) VALUES (?, ?, ?, ?, ?)",
                         (card_id, idx, "prop", json.dumps(p), p.get("my_pick")))
            idx += 1
    unmatched = [f"{g['away']} @ {g['home']}" for g in parsed["games"] if not g["game_id"]]
    unmatched += [f"Question {p['n']}" for p in props if not p.get("team") or not p.get("stat")]
    return jsonify({"ok": True, "card_id": card_id, "games": len(parsed["games"]), "props": len(props),
                    "unmatched": unmatched})


def _card_payload(conn, card):
    items = _items(conn, card["id"])
    games = {g["id"]: g for g in _week_games(conn, card["season"], card["week"], card["season_type"])}
    out, projected = [], 0
    for it in items:
        d = it["data"]
        g = games.get(d.get("game_id"))
        it["game"] = {k: g[k] for k in ("id", "start_date", "completed", "home_points", "away_points", "home_team",
                                         "away_team", "spread", "total")} if g else None
        it["kicked_off"] = bool(deps["kicked_off"](g)) if g else False
        it["locked"] = _locked(card, g)
        c = it["claude"]
        if g and it["kind"] != "prop":
            flip = d.get("flipped")
            it["market"] = {"home_spread": None if g["spread"] is None else (-g["spread"] if flip else g["spread"]),
                            "total": g["total"]}
        if c:
            if it["kind"] == "prop":
                it["claude_stale"] = c.get("pool_line") is not None and c.get("pool_line") != d.get("line")
            else:
                it["claude_stale"] = c.get("pool_spread") != d.get("home_spread") or c.get("pool_total") != d.get("total")
        if it["kind"] == "game" and c and c.get("proj_home") is not None:   # league rule: mid-major excluded
            projected += c["proj_home"] + c["proj_away"]
        out.append(it)
    # Score with the league's rules (League tab > Scoring rules), base points before combined-score / POTW.
    import league
    row = conn.execute("SELECT value FROM settings WHERE key='scoring'").fetchone()
    cfg = league.merged(json.loads(row["value"]) if row else None)
    mine = {str(it["idx"]): ({"pick": it["my_prop"]} if it["kind"] == "prop" else {"ats": it["my_ats"], "ou": it["my_ou"]})
            for it in out}
    theirs = {str(it["idx"]): ({"pick": (it["claude"] or {}).get("pick")} if it["kind"] == "prop" else
                               {"ats": (it["claude"] or {}).get("ats_pick"), "ou": (it["claude"] or {}).get("ou_pick")})
              for it in out if it["claude"]}
    my_score, claude_score = league.score_entry(out, mine, cfg), league.score_entry(out, theirs, cfg)
    picks = {it["idx"]: {"ats": it["my_ats"], "ou": it["my_ou"]} for it in out if it["kind"] != "prop"}
    card_games = [{**it["data"], "idx": it["idx"]} for it in out if it["kind"] != "prop"]
    props = [{**it["data"], "pick": it["my_prop"]} for it in out if it["kind"] == "prop"]
    tiebreak = card["tiebreaker"] if card["tiebreaker"] is not None else (round(projected) if projected else None)
    return {
        "card": {**{k: card[k] for k in ("id", "season", "week", "season_type", "has_tiebreaker", "tiebreaker", "deadline")},
                 "locked": past_deadline(card)},
        "items": out,
        "projected_combined": round(projected) if projected else None,
        "score": {"points": my_score["base"], "graded_possible": my_score["breakdown"]["graded"],
                  "correct": my_score["breakdown"]["correct"], "claude_points": claude_score["base"]},
        "export": pool.export_text(card_games, picks, props, tiebreak if card["has_tiebreaker"] else None),
        "tiebreaker_source": "mine" if card["tiebreaker"] is not None else ("claude" if projected else None),
        "scoring_note": (f"League scoring: spread {cfg['ats']}, O/U {cfg['ou']}, question {cfg['prop']}, mid-major "
                         f"{cfg['mid_major']}, N-Point Game per the League tab. Combined-score and POTW points show on the League tab."),
    }


@bp.get("/api/pool")
def get_card():
    season, week = request.args.get("season", type=int), request.args.get("week", type=int)
    season_type = request.args.get("season_type") or "regular"
    with db.tx() as conn:
        card = _card(conn, season, week, season_type)
        if not card:
            return jsonify({"card": None})
        return jsonify(_card_payload(conn, card))


def _replay_jobs(conn, game):
    """What the replay needs to look up: the venue (for the as-issued forecast) unless indoors."""
    venue = conn.execute("SELECT * FROM venues WHERE id=?", (game["venue_id"],)).fetchone() if game.get("venue_id") else None
    indoor = bool(venue and venue["dome"]) or bool(
        not game["neutral_site"] and (teams_mod.home_venue_override(game["home_team"]) or {}).get("dome"))
    return {"venue": dict(venue) if venue else None, "indoor": indoor}


def _gather_replay(game, jobs, as_of, conn_factory, searches=True):
    """Cutoff-safe weather / injuries / intel for a replay (network calls happen outside DB transactions)."""
    import injuries
    import intel
    import weather
    out = {}
    v = jobs["venue"]
    if not jobs["indoor"] and v is not None:
        try:
            out["weather"] = weather.forecast_as_of(v["latitude"], v["longitude"], game["start_date"], as_of)
        except Exception:
            out["weather"] = None
    if not searches:  # copy-for-Claude mode: the chat does the cutoff-limited searches itself
        return out
    try:
        rep = injuries.fetch_report(game, cutoff=as_of)
        out["injuries"] = {"report": rep["report"], "sources": rep["sources"], "fetched_at": f"as of {as_of.isoformat(timespec='minutes')}"}
    except Exception:
        pass
    key = as_of.isoformat(timespec="minutes")
    out["intel"] = {}
    for team in (game["away_team"], game["home_team"]):
        with conn_factory() as conn:
            data, _ = deps["intel_row"](conn, team, game["season"], key)
        if data is None:
            try:
                data = intel.fetch(team, game["season"], as_of)
                with conn_factory() as conn:
                    deps["save_intel"](conn, team, game["season"], key, data)
            except Exception:
                data = None
        if data:
            out["intel"][team] = data
    return out


def _item_context(item_id, searches=True, build=True):
    """Everything needed to predict one pool item. After the deadline: strict replay as of the deadline."""
    with db.tx() as conn:
        it, card = _card_for_item(conn, item_id)
        it, card = dict(it), dict(card)
        d = json.loads(it["data"])
        if d.get("game_id") is None:
            raise _err(f"Item {it['idx']} isn't matched to a game on the slate.")
        game = dict(conn.execute("SELECT * FROM games WHERE id=?", (d["game_id"],)).fetchone())
        replay = past_deadline(card)
        as_of = _parse_iso(card["deadline"]) if replay else _now()
        if not replay and deps["kicked_off"](game):
            raise _err(f"Item {it['idx']}: this game has already kicked off.")
        jobs = _replay_jobs(conn, game) if replay else None
    if not build:  # saving a pasted answer: the context went into the prompt already
        return {"it": it, "card": card, "d": d, "game": game, "replay": replay, "as_of": as_of,
                "context": {"audit": ["Answer pasted from a Claude chat; the prompt was built with this cutoff."] if replay else []}}
    overrides = _gather_replay(game, jobs, as_of, db.tx, searches=searches) if replay else None
    with db.tx() as conn:
        context = deps["build_context"](conn, game, as_of=as_of, replay=replay, overrides=overrides, full=True)
        context["lessons"] = lessons_for(conn, card["season"], card["week"], card["season_type"])
    if replay:
        context["market_now"] = {"home": game["home_team"], "spread": game.get("spread_open"), "total": game.get("total_open"),
                                 "label": "opening line (replay: closing line hidden)"}
    else:
        context["market_now"] = {"home": game["home_team"], "spread": game["spread"], "total": game["total"]}
    return {"it": it, "card": card, "d": d, "game": game, "context": context, "replay": replay, "as_of": as_of}


def _game_result(d, row):
    flip = d.get("flipped")
    side = row["ats_pick"]
    if flip:  # convert CFBD home/away back to the card's home/away
        side = "away" if side == "home" else "home"
    return {"ats_pick": side, "ou_pick": row["ou_pick"] if d.get("total") is not None else None,
            "proj_home": row["proj_away"] if flip else row["proj_home"],
            "proj_away": row["proj_home"] if flip else row["proj_away"],
            "confidence": row["confidence"], "reasoning": row["reasoning"],
            "key_factors": json.loads(row["key_factors"]), "conditions_impact": row["conditions_impact"],
            "model": row["model"], "pool_spread": d["home_spread"], "pool_total": d.get("total")}


def _save_claude(ctx, res):
    res.update({"replay": ctx["replay"], "as_of": ctx["as_of"].isoformat(timespec="minutes"),
                "audit": ctx["context"].get("audit", []), "predicted_at": _now().isoformat(timespec="minutes")})
    with db.tx() as conn:
        conn.execute("UPDATE pool_items SET claude=? WHERE id=?", (json.dumps(res), ctx["it"]["id"]))
    return res


@bp.post("/api/pool/items/<int:item_id>/predict")
def predict_item(item_id):
    """Before the deadline: live prediction. After it: strict replay as of the deadline (for trial runs)."""
    from predict import predict, predict_prop
    ctx = _item_context(item_id)
    d, game, context = ctx["d"], ctx["game"], ctx["context"]
    try:
        if ctx["it"]["kind"] == "prop":
            if d.get("line") is None:
                raise _err("Couldn't read the over/under number in this question.")
            res = predict_prop(d, game, context)
            res["pool_line"] = d["line"]
        else:
            res = _game_result(d, predict(_pool_game(game, d), game.get("notes") or "", context))
    except deps["ApiError"]:
        raise
    except Exception as e:
        raise _err(f"Prediction failed: {e}", 502)
    return jsonify(_save_claude(ctx, res))


def _selected_items(conn, card_id, only):
    card = conn.execute("SELECT * FROM pool_cards WHERE id=?", (card_id,)).fetchone()
    if not card:
        raise _err("Card not found", 404)
    rows = conn.execute("SELECT * FROM pool_items WHERE card_id=? ORDER BY idx", (card_id,)).fetchall()
    games = {g["id"]: g for g in _week_games(conn, card["season"], card["week"], card["season_type"])}
    replay = past_deadline(card)
    out = []
    for r in rows:
        d = json.loads(r["data"])
        if only and r["id"] not in only:
            continue
        g = games.get(d.get("game_id"))
        if g is None or (not replay and deps["kicked_off"](g)):
            continue
        out.append(r["id"])
    return card, out


def _numbering(conn, card_id):
    """Item numbers used in copy-for-Claude prompts: 1..N in card order."""
    rows = conn.execute("SELECT id FROM pool_items WHERE card_id=? ORDER BY idx", (card_id,)).fetchall()
    return {r["id"]: n for n, r in enumerate(rows, 1)}


def _only(arg):
    return {int(x) for x in str(arg).split(",") if x.strip().isdigit()} if arg else None


@bp.get("/api/pool/cards/<int:card_id>/prompt")
def card_prompt(card_id):
    """Copy-for-Claude: one prompt for the whole card (or ?items=<id,id>)."""
    import manual
    import predict
    with db.tx() as conn:
        card, ids = _selected_items(conn, card_id, _only(request.args.get("items")))
        number = _numbering(conn, card_id)
    if not ids:
        raise _err("Nothing to predict: no items matched to an un-kicked-off game.")
    sections, as_of, replay = [], None, False
    for item_id in ids:
        ctx = _item_context(item_id, searches=False)
        it, d, game, context = ctx["it"], ctx["d"], ctx["game"], ctx["context"]
        as_of, replay = context["as_of"], ctx["replay"]
        if it["kind"] == "prop":
            if d.get("line") is None:
                continue
            text = predict.prop_prompt(d, game, context)
        else:
            pg = _pool_game(game, d)
            if d.get("flipped"):
                text = (f"(Note: the card lists {d['away']} at {d['home']}; below uses the official home team. "
                        f"Name the team you pick, so orientation doesn't matter.)\n")
            else:
                text = ""
            text += predict.build_prompt(pg, game.get("notes") or "", context)
        sections.append({"idx": number[item_id], "kind": it["kind"], "teams": [game["home_team"], game["away_team"]], "text": text})
    title = f"{card['season']} week {card['week']} pick'em card" + (" (BLIND REPLAY)" if replay else "")
    return jsonify({"prompt": manual.card_prompt(sections, as_of, replay, title), "items": [s["idx"] for s in sections],
                    "replay": replay, "as_of": as_of})


@bp.post("/api/pool/cards/<int:card_id>/answer")
def card_answer(card_id):
    """Copy-for-Claude: save Claude's pasted answer (JSON array keyed by item number)."""
    import manual
    import predict
    body = request.get_json(force=True) or {}
    try:
        answers = manual.answers_by_item(manual.extract_json(body.get("answer")))
    except ValueError as e:
        raise _err(str(e))
    with db.tx() as conn:
        card, ids = _selected_items(conn, card_id, None)
        by_idx = {n: i for i, n in _numbering(conn, card_id).items()}
    if None in answers and len(answers) == 1 and body.get("item_id"):
        answers = {next(k for k, v in by_idx.items() if v == int(body["item_id"])): answers[None]}
    saved, errors = [], []
    for n, obj in sorted(answers.items(), key=lambda kv: (kv[0] is None, kv[0] or 0)):
        item_id = by_idx.get(n)
        if item_id is None:
            errors.append(f"Item {n}: no such item on the card.")
            continue
        if item_id not in ids:
            errors.append(f"Item {n}: locked (kicked off) or not matched to a game; skipped.")
            continue
        try:
            ctx = _item_context(item_id, build=False)
            d, game = ctx["d"], ctx["game"]
            if ctx["it"]["kind"] == "prop":
                res = predict.prop_from_data(obj, manual.MANUAL_MODEL)
                res["pool_line"] = d.get("line")
            else:
                pg = _pool_game(game, d)
                res = _game_result(d, predict.row_from_data(pg, obj, game.get("notes") or "", None, manual.MANUAL_MODEL))
            _save_claude(ctx, res)
            saved.append(n)
        except deps["ApiError"] as e:
            errors.append(f"Item {n}: {e}")
        except (ValueError, TypeError, KeyError) as e:
            errors.append(f"Item {n}: {e}")
    if not saved and errors:
        raise _err("Nothing saved. " + " ".join(errors))
    return jsonify({"saved": saved, "errors": errors})


@bp.post("/api/pool/items/<int:item_id>/pick")
def pick_item(item_id):
    body = request.get_json(force=True) or {}
    with db.tx() as conn:
        it, card = _card_for_item(conn, item_id)
        d = json.loads(it["data"])
        game = conn.execute("SELECT * FROM games WHERE id=?", (d.get("game_id"),)).fetchone() if d.get("game_id") else None
        if _locked(card, game):
            raise _err("Picks are locked for this game (pool deadline or kickoff passed).")
        if it["kind"] == "prop":
            v = body.get("pick")
            if v not in ("over", "under", None):
                raise _err("pick must be over or under")
            conn.execute("UPDATE pool_items SET my_prop=? WHERE id=?", (v, item_id))
        else:
            ats, ou = body.get("ats"), body.get("ou")
            if ats not in ("home", "away", None) or ou not in ("over", "under", None):
                raise _err("Invalid pick")
            conn.execute("UPDATE pool_items SET my_ats=?, my_ou=? WHERE id=?", (ats, ou, item_id))
    return jsonify({"ok": True})


def _num_field(body, key, required=False):
    v = body.get(key)
    if v in (None, ""):
        if required:
            raise _err(f"{key} is required")
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        raise _err(f"{key} must be a number")


@bp.post("/api/pool/cards")
def new_card():
    """Start a blank card for manual entry."""
    body = request.get_json(force=True) or {}
    season, week = int(body["season"]), int(body["week"])
    season_type = body.get("season_type") or "regular"
    with db.tx() as conn:
        if _card(conn, season, week, season_type):
            raise _err("This week already has a card.")
        conn.execute("INSERT INTO pool_cards (season, week, season_type, raw, has_tiebreaker, deadline) VALUES (?, ?, ?, '', 1, ?)",
                     (season, week, season_type, default_deadline(_week_games(conn, season, week, season_type))))
    return jsonify({"ok": True})


@bp.post("/api/pool/cards/<int:card_id>/items")
def add_item(card_id):
    body = request.get_json(force=True) or {}
    kind = body.get("kind") or "game"
    with db.tx() as conn:
        card = conn.execute("SELECT * FROM pool_cards WHERE id=?", (card_id,)).fetchone()
        if not card:
            raise _err("Card not found", 404)
        if past_deadline(card):
            raise _err("This week's picks are locked (deadline passed).")
        week_games = _week_games(conn, card["season"], card["week"], card["season_type"])
        idx = (conn.execute("SELECT MAX(idx) AS m FROM pool_items WHERE card_id=?", (card_id,)).fetchone()["m"] or 0) + 1
        now = _now().isoformat(timespec="minutes")
        if kind in ("game", "mid_major"):
            g = next((g for g in week_games if g["id"] == body.get("game_id")), None)
            if not g:
                raise _err("Pick a game from this week's slate.")
            data = {"kind": kind, "away": g["away_team"], "home": g["home_team"], "game_id": g["id"], "flipped": False,
                    "cfbd_away": g["away_team"], "cfbd_home": g["home_team"],
                    "home_spread": _num_field(body, "home_spread", required=True),
                    "total": _num_field(body, "total") if kind == "game" else None,
                    "weight": int(body.get("weight") or 1), "line_entered_at": now}
            conn.execute("INSERT INTO pool_items (card_id, idx, kind, data) VALUES (?, ?, ?, ?)",
                         (card_id, idx, kind, json.dumps(data)))
        elif kind == "prop":
            q = (body.get("question") or "").strip()
            if not q:
                raise _err("Type the question.")
            n = conn.execute("SELECT COUNT(*) AS c FROM pool_items WHERE card_id=? AND kind='prop'", (card_id,)).fetchone()["c"] + 1
            line = _num_field(body, "line")
            if line is None:
                lm = pool.LINE_RE.search(q)
                line = float(lm.group("line")) if lm else None
            if line is None:
                raise _err("Enter the over/under number.")
            data = pool.interpret_prop({"kind": "prop", "n": n, "question": q, "line": line, "unit": "", "my_pick": None},
                                       week_games, teams_mod.load_reference(card["season"]))
            data["line_entered_at"] = now
            conn.execute("INSERT INTO pool_items (card_id, idx, kind, data) VALUES (?, ?, 'prop', ?)",
                         (card_id, idx, json.dumps(data)))
        else:
            raise _err("kind must be game, mid_major or prop")
    return jsonify({"ok": True})


@bp.post("/api/pool/items/<int:item_id>/line")
def edit_line(item_id):
    """Enter or correct the pool's line (spread / over-under / bonus weight / prop number)."""
    body = request.get_json(force=True) or {}
    with db.tx() as conn:
        it, card = _card_for_item(conn, item_id)
        if past_deadline(card):
            raise _err("This week's picks are locked (deadline passed).")
        d = json.loads(it["data"])
        if it["kind"] == "prop":
            d["line"] = _num_field(body, "line", required=True)
        else:
            d["home_spread"] = _num_field(body, "home_spread", required=True)
            if it["kind"] == "game":
                d["total"] = _num_field(body, "total")
            if body.get("weight") not in (None, ""):
                d["weight"] = max(1, int(body["weight"]))
        d["line_entered_at"] = _now().isoformat(timespec="minutes")
        conn.execute("""UPDATE pool_items SET data=?, ats_result=NULL, ou_result=NULL, prop_result=NULL,
                        claude_ats_result=NULL, claude_ou_result=NULL, claude_prop_result=NULL WHERE id=?""",
                     (json.dumps(d), item_id))
    return jsonify({"ok": True})


@bp.delete("/api/pool/items/<int:item_id>")
def delete_item(item_id):
    with db.tx() as conn:
        it, card = _card_for_item(conn, item_id)
        if past_deadline(card):
            raise _err("This week's picks are locked (deadline passed).")
        conn.execute("DELETE FROM pool_items WHERE id=?", (item_id,))
    return jsonify({"ok": True})


@bp.post("/api/pool/cards/<int:card_id>/deadline")
def set_deadline(card_id):
    v = (request.get_json(force=True) or {}).get("deadline")
    if v:
        try:
            v = _parse_iso(v).astimezone(timezone.utc).isoformat(timespec="minutes")
        except ValueError:
            raise _err("Invalid deadline")
    with db.tx() as conn:
        conn.execute("UPDATE pool_cards SET deadline=? WHERE id=?", (v or None, card_id))
    return jsonify({"ok": True})


@bp.post("/api/pool/items/<int:item_id>/actual")
def set_prop_actual(item_id):
    """Enter a question's real result by hand (player props, or when automatic grading can't read it)."""
    body = request.get_json(force=True) or {}
    with db.tx() as conn:
        it, card = _card_for_item(conn, item_id)
        if it["kind"] != "prop":
            raise _err("Only questions take a manual result.")
        d = json.loads(it["data"])
        actual = _num_field(body, "value")
        c = json.loads(it["claude"]) if it["claude"] else {}
        conn.execute("UPDATE pool_items SET prop_actual=?, prop_result=?, claude_prop_result=? WHERE id=?",
                     (actual, pool.grade_prop(it["my_prop"], d.get("line"), actual) if actual is not None else None,
                      pool.grade_prop(c.get("pick"), d.get("line"), actual) if actual is not None else None, item_id))
    return jsonify({"ok": True})


@bp.post("/api/pool/cards/<int:card_id>/tiebreaker")
def set_tiebreaker(card_id):
    v = (request.get_json(force=True) or {}).get("value")
    with db.tx() as conn:
        card = conn.execute("SELECT * FROM pool_cards WHERE id=?", (card_id,)).fetchone()
        if card and past_deadline(card):
            raise _err("This week's picks are locked (deadline passed).")
    try:
        v = int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        raise _err("Tiebreaker must be a whole number")
    with db.tx() as conn:
        conn.execute("UPDATE pool_cards SET tiebreaker=?, has_tiebreaker=1 WHERE id=?", (v, card_id))
    return jsonify({"ok": True})


@bp.post("/api/pool/cards/<int:card_id>/grade")
def grade_card(card_id):
    from backtest.collect import parse_box
    with db.tx() as conn:
        card = conn.execute("SELECT * FROM pool_cards WHERE id=?", (card_id,)).fetchone()
        if not card:
            raise _err("Card not found", 404)
    games = cfbd.fetch_games(card["season"], card["week"], card["season_type"])
    box = parse_box(cfbd._get("/games/teams", year=card["season"], week=card["week"],
                              seasonType=card["season_type"], classification="fbs"))
    graded = 0
    with db.tx() as conn:
        for g in games:
            db.upsert_game(conn, g)
        by_id = {g["id"]: g for g in games}
        for it in _items(conn, card_id):
            d = it["data"]
            g = by_id.get(d.get("game_id"))
            if it["kind"] == "prop":
                actual = pool.prop_actual(d, box)
                if actual is None:
                    continue
                c = it["claude"] or {}
                conn.execute("UPDATE pool_items SET prop_actual=?, prop_result=?, claude_prop_result=? WHERE id=?",
                             (actual, pool.grade_prop(it["my_prop"], d["line"], actual),
                              pool.grade_prop(c.get("pick"), d["line"], actual), it["id"]))
                graded += 1
            elif g and g["completed"]:
                keys = ("points", "rush_yds", "pass_yds", "total_yds", "sacks", "turnovers")
                hb, ab = box.get((g["id"], g["home_team"]), {}), box.get((g["id"], g["away_team"]), {})
                if d.get("flipped"):
                    hb, ab = ab, hb
                d["actual"] = {"home": {k: hb.get(k) for k in keys}, "away": {k: ab.get(k) for k in keys}}
                conn.execute("UPDATE pool_items SET data=? WHERE id=?", (json.dumps(d), it["id"]))
                mine = pool.grade_game_item({**d, "my_ats": it["my_ats"], "my_ou": it["my_ou"]}, g)
                c = it["claude"] or {}
                theirs = pool.grade_game_item({**d, "my_ats": c.get("ats_pick"), "my_ou": c.get("ou_pick")}, g)
                conn.execute("""UPDATE pool_items SET ats_result=?, ou_result=?, claude_ats_result=?, claude_ou_result=?
                                WHERE id=?""", (*mine, *theirs, it["id"]))
                graded += 1
        payload = _card_payload(conn, card)
    return jsonify({"graded": graded, **payload})


def _week_key(season_type, week):
    return (0 if season_type == "regular" else 1, week)


def _season_rows(conn, season, before=None):
    """All Claude pick rows for a season, optionally only from cards before (season_type, week)."""
    import review
    rows = []
    for c in conn.execute("SELECT * FROM pool_cards WHERE season=? ORDER BY week", (season,)).fetchall():
        if before and _week_key(c["season_type"], c["week"]) >= before:
            continue
        rows += [{**r, "week": c["week"]} for r in review.week_items(_card_payload(conn, c)["items"])]
    return rows


def lessons_for(conn, season, week, season_type="regular"):
    """Active lessons + calibration facts from EARLIER weeks only (keeps replays blind)."""
    import review
    key = _week_key(season_type, week)
    out = []
    for r in conn.execute("SELECT * FROM lessons WHERE season=? AND active=1 ORDER BY week DESC, id DESC", (season,)).fetchall():
        if _week_key(r["season_type"], r["week"]) < key:
            out.append(f"[week {r['week']}, {r['applies_to']}] {r['text']}")
    out = out[:10]
    out += review.calibration_lines(review.stats(_season_rows(conn, season, before=key)))
    return out


def _review_inputs(card_id):
    import review
    with db.tx() as conn:
        card = conn.execute("SELECT * FROM pool_cards WHERE id=?", (card_id,)).fetchone()
        if not card:
            raise _err("Card not found", 404)
        rows = review.week_items(_card_payload(conn, card)["items"])
        if not any(r.get("result") in ("W", "L", "P") for r in rows):
            raise _err("Grade the card first (no graded Claude picks yet).")
        week_stats = review.stats(rows)
        prior = lessons_for(conn, card["season"], card["week"], card["season_type"])
    return dict(card), rows, week_stats, prior


def _save_review(card, week_stats, out):
    data = {"stats": week_stats, **out}
    with db.tx() as conn:
        conn.execute("""INSERT INTO reviews (card_id, data) VALUES (?, ?)
                        ON CONFLICT(card_id) DO UPDATE SET data=excluded.data, created_at=datetime('now')""",
                     (card["id"], json.dumps(data)))
        conn.execute("DELETE FROM lessons WHERE card_id=?", (card["id"],))
        for l in out["lessons"]:
            conn.execute("""INSERT INTO lessons (card_id, season, week, season_type, text, applies_to, evidence)
                            VALUES (?, ?, ?, ?, ?, ?, ?)""",
                         (card["id"], card["season"], card["week"], card["season_type"], l["text"], l["applies_to"], l["evidence"]))
    return data


@bp.post("/api/pool/cards/<int:card_id>/review")
def review_card(card_id):
    import review
    card, rows, week_stats, prior = _review_inputs(card_id)
    try:
        out = review.claude_review(rows, week_stats, card["season"], card["week"], prior)
    except Exception as e:
        raise _err(f"Review failed: {e}", 502)
    return jsonify(_save_review(card, week_stats, out))


@bp.get("/api/pool/cards/<int:card_id>/review/prompt")
def review_prompt(card_id):
    import manual
    import review
    card, rows, week_stats, prior = _review_inputs(card_id)
    return jsonify({"prompt": manual.review_prompt(rows, week_stats, card["season"], card["week"], prior,
                                                   review.REVIEW_SYSTEM, review._schema(len(rows)))})


@bp.post("/api/pool/cards/<int:card_id>/review/answer")
def review_answer(card_id):
    import manual
    import review
    card, rows, week_stats, prior = _review_inputs(card_id)
    try:
        out = review.finalize(manual.extract_json((request.get_json(force=True) or {}).get("answer")))
    except ValueError as e:
        raise _err(str(e))
    return jsonify(_save_review(card, week_stats, out))


@bp.get("/api/review")
def season_review():
    import review
    season = request.args.get("season", type=int)
    with db.tx() as conn:
        weeks = []
        for c in conn.execute("SELECT * FROM pool_cards WHERE season=? ORDER BY week", (season,)).fetchall():
            rows = review.week_items(_card_payload(conn, c)["items"])
            rv = conn.execute("SELECT data FROM reviews WHERE card_id=?", (c["id"],)).fetchone()
            weeks.append({"card_id": c["id"], "week": c["week"], "season_type": c["season_type"],
                          "stats": review.stats(rows), "review": json.loads(rv["data"]) if rv else None})
        season_stats = review.stats(_season_rows(conn, season))
        lessons = [dict(r) for r in conn.execute("SELECT * FROM lessons WHERE season=? ORDER BY week DESC, id", (season,))]
    return jsonify({"season": season, "season_stats": season_stats, "weeks": weeks, "lessons": lessons,
                    "calibration_lines": review.calibration_lines(season_stats)})


@bp.post("/api/lessons/<int:lesson_id>")
def toggle_lesson(lesson_id):
    active = 1 if (request.get_json(force=True) or {}).get("active") else 0
    with db.tx() as conn:
        conn.execute("UPDATE lessons SET active=? WHERE id=?", (active, lesson_id))
    return jsonify({"ok": True})


@bp.delete("/api/lessons/<int:lesson_id>")
def delete_lesson(lesson_id):
    with db.tx() as conn:
        conn.execute("DELETE FROM lessons WHERE id=?", (lesson_id,))
    return jsonify({"ok": True})


@bp.get("/api/pool/record")
def pool_record():
    season = request.args.get("season", type=int)
    with db.tx() as conn:
        cards = conn.execute("SELECT * FROM pool_cards WHERE season=? ORDER BY week", (season,)).fetchall()
        weeks = []
        totals = {k: {"W": 0, "L": 0, "P": 0} for k in ("ats", "ou", "prop", "claude_ats", "claude_ou", "claude_prop")}
        for c in cards:
            p = _card_payload(conn, c)
            weeks.append({"week": c["week"], "season_type": c["season_type"], **p["score"]})
            for it in p["items"]:
                for k, col in (("ats", "ats_result"), ("ou", "ou_result"), ("prop", "prop_result"),
                               ("claude_ats", "claude_ats_result"), ("claude_ou", "claude_ou_result"),
                               ("claude_prop", "claude_prop_result")):
                    if it[col] in totals[k]:
                        totals[k][it[col]] += 1
    return jsonify({"season": season, "weeks": weeks, "totals": totals})
