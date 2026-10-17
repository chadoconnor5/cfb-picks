"""Claude's picks and weekly review for cards saved by the website (state.json in the private data repo).

Normal use (no API key): a Claude Code session does the research itself (see CLAUDE.md):
    python cloud.py --state ../cfb-picks-data/state.json --status                           # what's waiting
    python cloud.py --state ../cfb-picks-data/state.json --prompt "2026|regular|8" > card.md  # the card prompt
    python cloud.py --state ../cfb-picks-data/state.json --answers answers.json --card "2026|regular|8" --patch patch.json
    python cloud.py --state ../cfb-picks-data/state.json --review-prompt "2026|regular|8" > review.md
    python cloud.py --state ../cfb-picks-data/state.json --review-answer review.json --card "2026|regular|8" --patch patch.json
    python cloud.py --state ../cfb-picks-data/state.json --apply patch.json                  # merge into state.json

The prompts are the same text the site's "Copy for Claude" buttons give (docs/logic.js cardPrompt), and the
answers are validated the same way. --apply merges only Claude's results into a fresh copy of state.json,
so edits made on the site in the meantime are kept.

With ANTHROPIC_API_KEY set, --card / --review / --auto ask Claude through the API instead.
"""
import argparse
import json
import os
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.getenv("SITE_DATA_DIR", os.path.join(HERE, "docs", "data"))
MODEL = os.getenv("PICKS_CLAUDE_MODEL") or "claude-opus-5-5"
EFFORT = os.getenv("PICKS_CLAUDE_EFFORT") or "high"
MAX_SEARCHES = int(os.getenv("PICKS_MAX_SEARCHES") or 6)
WORKERS = int(os.getenv("PICKS_CLAUDE_WORKERS") or 3)

import grading  # noqa: E402
import manual  # noqa: E402
import pool  # noqa: E402
import predict  # noqa: E402
import review  # noqa: E402

API_SYSTEM = ("You are a sharp, disciplined college football handicapper working for a pick'em pool player. "
              "Follow the instructions in the message exactly: research with web search first, then answer. "
              "Finish with the JSON answer in a ```json code block.")


# ---------- small helpers (same rules as docs/store.js) ----------

def now():
    return datetime.now(timezone.utc)


def iso_min(d=None):
    return (d or now()).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M") + "+00:00"


def parse_iso(s):
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def new_id():
    return int(time.time() * 1000) * 100 + random.randint(0, 99)


def kicked_off(g):
    if not g.get("start_date"):
        return bool(g.get("completed"))
    return bool(g.get("completed")) or now() >= parse_iso(g["start_date"])


def past_deadline(card):
    return bool(card.get("deadline")) and now() >= parse_iso(card["deadline"])


def sorted_items(card):
    return sorted(card["items"], key=lambda it: it["idx"])


def numbering(card):
    return {it["id"]: n for n, it in enumerate(sorted_items(card), 1)}


def pool_game(game, d):
    g = dict(game)
    g["spread"] = -d["home_spread"] if d.get("flipped") else d["home_spread"]
    g["total"] = d.get("total")
    g["spread_open"] = g["total_open"] = None
    return g


def card_key(card):
    return f"{card['season']}|{card.get('season_type') or 'regular'}|{card['week']}"


_weeks = {}


def week_data(season, week, season_type):
    key = (int(season), int(week), season_type or "regular")
    if key not in _weeks:
        path = os.path.join(DATA, str(season), f"{key[2]}-w{int(week):02d}.json")
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            d["byId"] = {g["id"]: g for g in d["games"]}
        except OSError:
            d = None
        _weeks[key] = d
    return _weeks[key]


def market_for(card, game):
    ctx = game.get("ctx") or {}
    if past_deadline(card):
        line = ctx.get("line") or {"spread": game.get("spread_open"), "total": game.get("total_open"), "label": "opening line"}
        return {"home": game["home_team"], "spread": line.get("spread"), "total": line.get("total"),
                "label": line.get("label") if ctx.get("replay") else "line as of the pool deadline"}
    return {"home": game["home_team"], "spread": game.get("spread"), "total": game.get("total")}


def prop_prompt(prop, game, prop_stats, weather_text, labels):
    """Same text as propPrompt() in docs/logic.js."""
    labels = labels or {}
    line = prop.get("line")
    lines = [f"Question: {prop['question']}", f"Line: {float(line) if line is not None else None}",
             f"Interpreted as: {labels.get(prop.get('stat')) or prop.get('stat') or 'unknown stat'} - "
             + ("both teams combined" if prop.get("scope") == "combined" else f"{prop.get('team')} only")
             + (f" (opponent: {prop.get('opponent')})" if prop.get("opponent") else "")]
    if game:
        total = game.get("total")
        lines.append(f"Game: {game['away_team']} at {game['home_team']}, line {grading.format_spread(game['home_team'], game.get('spread'))}, "
                     f"total {float(total) if total is not None else None}, kickoff {game.get('start_date')}")
    if prop_stats:
        key = prop.get("stat")
        for side in ("away", "home"):
            t = prop_stats.get(side)
            if not t:
                continue
            if key:
                f_, a_ = t["for"].get(key), t["against"].get(key)
                lines.append(f"{t['team']} {labels.get(key) or key} per game this season: "
                             + (f"avg {_js(f_['avg'])}, last 3 {_js(f_['last3'])}, range {_js(f_['min'])}-{_js(f_['max'])} (n={f_['n']})" if f_ else "n/a")
                             + "; allowed to opponents: " + (f"avg {_js(a_['avg'])}, last 3 {_js(a_['last3'])}" if a_ else "n/a"))
            lines.append(f"{t['team']} game log: " + "; ".join(
                f"vs {g['opponent']}: {(_js(g.get(key)) if g.get(key) is not None else 'None') if key else ''}" for g in t["games"][-6:]))
    if weather_text:
        lines += ["Weather/style:", weather_text]
    return "\n".join(lines)


def _js(v):
    """A number as JavaScript's String() prints it (the site's prompt text)."""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def notes_line(state, game_id):
    n = ((state.get("slate") or {}).get("notes") or {}).get(str(game_id), "") or ""
    return f"My notes: {n.strip() or '(none)'}"


def item_section(state, card, week, it, num, labels):
    d, game = it["data"], week["byId"][d_id(it)]
    ctx = game.get("ctx")
    replay = past_deadline(card)
    market = market_for(card, game)
    if it["kind"] == "prop":
        if d.get("line") is None:
            return None
        g = {**game, "spread": market["spread"] if replay else game.get("spread"),
             "total": market["total"] if replay else game.get("total")}
        text = prop_prompt(d, g, (ctx or {}).get("prop_stats"), (ctx or {}).get("weather_text"), labels)
    else:
        pg = pool_game(game, d)
        text = (f"(Note: the card lists {d['away']} at {d['home']}; below uses the official home team. "
                "Name the team you pick, so orientation doesn't matter.)\n") if d.get("flipped") else ""
        text += "\n".join(predict.header_lines(pg, {"market_now": market})) + "\n" + \
            (ctx["text"] if ctx else "\n(No pre-game data was saved for this game; research it.)") + "\n\n" + notes_line(state, game["id"])
    return {"idx": num, "kind": it["kind"], "teams": [game["home_team"], game["away_team"]], "text": text}


def d_id(it):
    return it["data"].get("game_id")


def eligible(card, week, only=None):
    replay = past_deadline(card)
    out = []
    for it in sorted_items(card):
        if only and it["id"] not in only:
            continue
        g = week["byId"].get(d_id(it)) if week else None
        if g and (replay or not kicked_off(g)):
            out.append(it)
    return out


# ---------- results, review rows and lessons (same as cardPayload in docs/store.js) ----------

def payload_items(card):
    week = week_data(card["season"], card["week"], card.get("season_type"))
    games = week["byId"] if week else {}
    out = []
    for raw in sorted_items(card):
        it = json.loads(json.dumps(raw))
        d = it["data"]
        g = games.get(d.get("game_id"))
        it["game"] = {k: g.get(k) for k in ("id", "start_date", "completed", "home_points", "away_points", "home_team",
                                             "away_team", "spread", "total")} if g else None
        c = it.get("claude")
        if g and it["kind"] != "prop":
            it["market"] = {"home_spread": None if g.get("spread") is None else (-g["spread"] if d.get("flipped") else g["spread"]),
                            "total": g.get("total")}
        for k in ("ats_result", "ou_result", "claude_ats_result", "claude_ou_result", "prop_result", "claude_prop_result"):
            it[k] = None
        if it["kind"] == "prop":
            actual = it.get("prop_actual_manual")
            if actual is None and g and g.get("box") and d.get("stat") and d.get("team"):
                side = lambda t: "home" if t == g["home_team"] else "away" if t == g["away_team"] else None  # noqa: E731
                mine = (g["box"] or {}).get(side(d["team"]) or "") or {}
                if mine.get(d["stat"]) is not None:
                    if d.get("scope") == "combined":
                        other = (g["box"] or {}).get(side(d.get("opponent")) or "") or {}
                        actual = mine[d["stat"]] + other[d["stat"]] if other.get(d["stat"]) is not None else None
                    else:
                        actual = mine[d["stat"]]
            it["prop_actual"] = actual
            if actual is not None:
                it["prop_result"] = pool.grade_prop(it.get("my_prop"), d.get("line"), actual)
                it["claude_prop_result"] = pool.grade_prop((c or {}).get("pick"), d.get("line"), actual)
        elif g and g.get("completed"):
            it["ats_result"], it["ou_result"] = pool.grade_game_item({**d, "my_ats": it.get("my_ats"), "my_ou": it.get("my_ou")}, g)
            it["claude_ats_result"], it["claude_ou_result"] = pool.grade_game_item(
                {**d, "my_ats": (c or {}).get("ats_pick"), "my_ou": (c or {}).get("ou_pick")}, g)
            if g.get("box"):
                hb, ab = g["box"].get("home") or {}, g["box"].get("away") or {}
                d["actual"] = {"home": ab, "away": hb} if d.get("flipped") else {"home": hb, "away": ab}
        out.append(it)
    return out


def week_key(season_type, week):
    return (0 if (season_type or "regular") == "regular" else 1, int(week))


def season_rows(state, season, before=None):
    rows = []
    cards = sorted((c for c in state.get("cards", {}).values() if c["season"] == season),
                   key=lambda c: week_key(c.get("season_type"), c["week"]))
    for c in cards:
        if before and not week_key(c.get("season_type"), c["week"]) < before:
            continue
        rows += [{**r, "week": c["week"]} for r in review.week_items(payload_items(c))]
    return rows


def lessons_for(state, season, week, season_type):
    key = week_key(season_type, week)
    ls = [l for l in state.get("lessons", []) if l["season"] == season and l.get("active")
          and week_key(l.get("season_type"), l["week"]) < key]
    ls.sort(key=lambda l: (-l["week"], -l["id"]))
    out = [f"[week {l['week']}, {l['applies_to']}] {l['text']}" for l in ls[:10]]
    return out + review.calibration_lines(review.stats(season_rows(state, season, key)))


# ---------- Claude ----------

def ask_claude(prompt):
    """One research-then-answer call with web search. Returns (parsed JSON, model, sources)."""
    import anthropic
    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": prompt}]
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": MAX_SEARCHES}]
    resp = None
    for _ in range(6):
        resp = client.beta.messages.create(
            model=MODEL, max_tokens=16000, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            output_config={"effort": EFFORT}, system=API_SYSTEM, tools=tools, messages=messages)
        if resp.stop_reason != "pause_turn":
            break
        messages.append({"role": "assistant", "content": resp.content})
    if resp.stop_reason == "refusal":
        raise RuntimeError("Claude declined to answer.")
    blocks = list(resp.content)
    last_search = max((i for i, b in enumerate(blocks) if b.type.endswith("tool_result")), default=-1)
    final = [b for b in blocks[last_search + 1:] if b.type == "text"]
    text = "".join(b.text for b in final)
    seen, sources = set(), []
    for b in blocks:
        for c in getattr(b, "citations", None) or []:
            url = getattr(c, "url", None)
            if url and url not in seen:
                seen.add(url)
                sources.append({"url": url, "title": getattr(c, "title", None) or url})
    return manual.extract_json(text), getattr(resp, "model", MODEL), sources[:8]


def game_result(d, row):
    flip = d.get("flipped")
    side = row["ats_pick"]
    if flip:
        side = "away" if side == "home" else "home"
    return {"ats_pick": side, "ou_pick": row["ou_pick"] if d.get("total") is not None else None,
            "proj_home": row["proj_away"] if flip else row["proj_home"], "proj_away": row["proj_home"] if flip else row["proj_away"],
            "confidence": row["confidence"], "reasoning": row["reasoning"], "key_factors": json.loads(row["key_factors"]),
            "conditions_impact": row["conditions_impact"], "model": row["model"],
            "pool_spread": d["home_spread"], "pool_total": d.get("total")}


def card_prompt(state, key, item_ids=None):
    """The whole-card prompt (same text as the site's "Copy whole card for Claude")."""
    card = state["cards"].get(key)
    if not card:
        raise SystemExit(f"No card {key} in state.json")
    week = week_data(card["season"], card["week"], card.get("season_type"))
    if not week:
        raise SystemExit(f"No game data for {key}")
    labels = load_prompts().get("stat_labels") or {}
    num = numbering(card)
    replay = past_deadline(card)
    sections = [sec for it in eligible(card, week, set(item_ids) if item_ids else None)
                if (sec := item_section(state, card, week, it, num[it["id"]], labels))]
    if not sections:
        raise SystemExit("Nothing to predict (no items matched to an un-kicked-off game).")
    title = f"{card['season']} week {card['week']} pick'em card" + (" (BLIND REPLAY)" if replay else "")
    return manual.card_prompt(sections, card.get("deadline") if replay else None, replay, title,
                              lessons_for(state, card["season"], card["week"], card.get("season_type")))


def answers_patch(state, key, data, model="claude (Claude Code)", sources=None):
    """Validate a JSON answer (array keyed by item number) for a card and turn it into a patch."""
    card = state["cards"].get(key)
    week = week_data(card["season"], card["week"], card.get("season_type"))
    num = numbering(card)
    by_num = {n: i for i, n in num.items()}
    ok_ids = {it["id"] for it in eligible(card, week)}
    replay = past_deadline(card)
    items, errors = {}, []
    for n, obj in sorted(manual.answers_by_item(data).items(), key=lambda kv: (kv[0] is None, kv[0] or 0)):
        item_id = by_num.get(n)
        if item_id is None:
            errors.append(f"Item {n}: no such item on the card.")
            continue
        if item_id not in ok_ids:
            errors.append(f"Item {n}: locked (kicked off) or not matched to a game; skipped.")
            continue
        it = next(i for i in card["items"] if i["id"] == item_id)
        d, game = it["data"], week["byId"][d_id(it)]
        try:
            if it["kind"] == "prop":
                res = predict.prop_from_data(obj, model)
                res["pool_line"] = d.get("line")
            else:
                res = game_result(d, predict.row_from_data(pool_game(game, d), obj, model=model))
        except (ValueError, TypeError, KeyError) as e:
            errors.append(f"Item {n}: {e}")
            continue
        ctx = game.get("ctx") or {}
        res.update({"replay": replay, "as_of": card.get("deadline") if replay else iso_min(), "sources": (sources or {}).get(n, []),
                    "audit": (list(ctx.get("audit") or []) + ["Researched by Claude with web search, limited to sources from before the cutoff."]) if replay else [],
                    "predicted_at": iso_min()})
        items[str(item_id)] = res
    msg = f"Claude picked {len(items)} item(s)." + (" " + " ".join(errors) if errors else "")
    return {"cards": {key: {"items": items, "claude_job": {"status": "done" if items else "error", "finished_at": iso_min(),
                                                            "message": msg, "saved": len(items), "errors": errors}}}}


def review_prompt_text(state, key):
    card = state["cards"].get(key)
    rows = review.week_items(payload_items(card))
    if not any(r.get("result") in ("W", "L", "P") for r in rows):
        raise SystemExit(f"{key}: no graded Claude picks yet")
    return manual.review_prompt(rows, review.stats(rows), card["season"], card["week"],
                                lessons_for(state, card["season"], card["week"], card.get("season_type")),
                                review.REVIEW_SYSTEM, review._schema(len(rows)))


def status_report(state):
    lines = []
    for key, card in sorted((state.get("cards") or {}).items(), key=lambda kv: week_key(kv[1].get("season_type"), kv[1]["week"])):
        week = week_data(card["season"], card["week"], card.get("season_type"))
        n = len(card["items"])
        have = sum(1 for it in card["items"] if it.get("claude"))
        open_ = len(eligible(card, week)) if week else 0
        mine = sum(1 for it in card["items"] if it.get("my_ats") or it.get("my_ou") or it.get("my_prop"))
        games = [week["byId"].get(d_id(it)) for it in card["items"] if it["kind"] != "prop"] if week else []
        final = bool(games) and all(g and g.get("completed") for g in games)
        lines.append(f"{key}: {n} items, owner picks on {mine}, Claude picks on {have}, {open_} still predictable, "
                     f"deadline {card.get('deadline')} ({'passed' if past_deadline(card) else 'open'}), "
                     f"{'all games final' if final else 'games pending'}, review {'done' if key in (state.get('reviews') or {}) else 'not yet'}")
    return "\n".join(lines) or "No cards saved yet."


def predict_card(state, key, item_ids=None, ask=ask_claude):
    """Claude's picks for a card (or some of its items). Returns a patch."""
    card = state["cards"].get(key)
    if not card:
        raise SystemExit(f"No card {key} in state.json")
    week = week_data(card["season"], card["week"], card.get("season_type"))
    if not week:
        raise SystemExit(f"No game data for {key}")
    labels = load_prompts().get("stat_labels") or {}
    lessons = lessons_for(state, card["season"], card["week"], card.get("season_type"))
    num = numbering(card)
    replay = past_deadline(card)
    title = f"{card['season']} week {card['week']} pick'em card" + (" (BLIND REPLAY)" if replay else "")
    as_of = card.get("deadline") if replay else None
    jobs = []
    for it in eligible(card, week, set(item_ids) if item_ids else None):
        sec = item_section(state, card, week, it, num[it["id"]], labels)
        if sec:
            jobs.append((it, sec, manual.card_prompt([sec], as_of, replay, title, lessons)))

    def run(job):
        it, sec, prompt = job
        d = it["data"]
        game = week["byId"][d_id(it)]
        try:
            data, model, sources = ask(prompt)
            obj = list(manual.answers_by_item(data).values())[0]
            if it["kind"] == "prop":
                res = predict.prop_from_data(obj, model)
                res["pool_line"] = d.get("line")
            else:
                res = game_result(d, predict.row_from_data(pool_game(game, d), obj, model=model))
            ctx = game.get("ctx") or {}
            res.update({"replay": replay, "as_of": as_of if replay else iso_min(), "sources": sources,
                        "audit": (list(ctx.get("audit") or []) + ["Researched by Claude with web search, told to use only sources from before the cutoff."]) if replay else [],
                        "predicted_at": iso_min()})
            return it["id"], res, None
        except Exception as e:   # one bad item shouldn't sink the card
            return it["id"], None, f"Item {sec['idx']}: {e}"

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        results = list(ex.map(run, jobs))
    items = {str(i): r for i, r, _ in results if r}
    errors = [e for _, _, e in results if e]
    status = "done" if items and not errors else ("partial" if items else "error")
    msg = (f"Claude picked {len(items)} item(s)." if items else "Claude couldn't pick any items.") + (" " + " ".join(errors) if errors else "")
    if not jobs:
        status, msg = "error", "Nothing to predict (no items matched to an un-kicked-off game)."
    return {"cards": {key: {"items": items, "claude_job": {"status": status, "finished_at": iso_min(), "message": msg,
                                                            "saved": len(items), "errors": errors}}}}


def review_card(state, key, ask_review=None):
    card = state["cards"].get(key)
    rows = review.week_items(payload_items(card))
    if not any(r.get("result") in ("W", "L", "P") for r in rows):
        raise SystemExit(f"{key}: no graded Claude picks yet")
    week_stats = review.stats(rows)
    prior = lessons_for(state, card["season"], card["week"], card.get("season_type"))
    out = review.finalize((ask_review or review.claude_review)(rows, week_stats, card["season"], card["week"], prior))
    lessons = [{"id": new_id(), "card_key": key, "season": card["season"], "week": card["week"],
                "season_type": card.get("season_type") or "regular", "text": l["text"], "applies_to": l["applies_to"],
                "evidence": l["evidence"], "active": 1, "created_at": iso_min()} for l in out["lessons"]]
    return {"reviews": {key: {"stats": week_stats, **out, "created_at": iso_min()}}, "lessons_replace": {key: lessons},
            "cards": {key: {"items": {}, "review_job": {"status": "done", "finished_at": iso_min(),
                                                        "message": f"Review saved with {len(lessons)} lesson(s)."}}}}


def due(state):
    """(predict keys, review keys) the scheduled run should handle."""
    to_predict, to_review = [], []
    for key, card in (state.get("cards") or {}).items():
        week = week_data(card["season"], card["week"], card.get("season_type"))
        if not week:
            continue
        job = card.get("claude_job") or {}
        recent = job.get("requested_at") and now() - parse_iso(job["requested_at"]) < timedelta(minutes=40) and job.get("status") == "requested"
        if not past_deadline(card) and not recent:
            if any(not it.get("claude") for it in eligible(card, week)):
                to_predict.append(key)
        games = [week["byId"].get(d_id(it)) for it in card["items"] if it["kind"] != "prop"]
        finished = games and all(g and g.get("completed") for g in games)
        if finished and key not in (state.get("reviews") or {}) and any(it.get("claude") for it in card["items"]):
            last = max(parse_iso(g["start_date"]) for g in games if g.get("start_date"))
            if now() - last > timedelta(hours=8):
                to_review.append(key)
    return to_predict, to_review


def merge_patches(patches):
    out = {"cards": {}, "reviews": {}, "lessons_replace": {}}
    for p in patches:
        for k, v in p.get("cards", {}).items():
            c = out["cards"].setdefault(k, {"items": {}})
            c["items"].update(v.get("items") or {})
            for f in ("claude_job", "review_job"):
                if f in v:
                    c[f] = v[f]
        out["reviews"].update(p.get("reviews") or {})
        out["lessons_replace"].update(p.get("lessons_replace") or {})
    return out


def apply_patch(state, patch):
    """Merge Claude's results into (a fresh copy of) state.json without touching anything else."""
    for key, cp in (patch.get("cards") or {}).items():
        card = state.get("cards", {}).get(key)
        if not card:
            continue
        by_id = {str(it["id"]): it for it in card["items"]}
        for item_id, res in (cp.get("items") or {}).items():
            if item_id in by_id:
                by_id[item_id]["claude"] = res
        for f in ("claude_job", "review_job"):
            if f in cp:
                card[f] = {**(card.get(f) or {}), **cp[f]}
    state.setdefault("reviews", {}).update(patch.get("reviews") or {})
    for key, lessons in (patch.get("lessons_replace") or {}).items():
        state["lessons"] = [l for l in state.get("lessons", []) if l.get("card_key") != key] + lessons
    state["savedAt"] = now().isoformat()
    return state


_prompts = None


def load_prompts():
    global _prompts
    if _prompts is None:
        try:
            with open(os.path.join(DATA, "prompts.json"), encoding="utf-8") as f:
                _prompts = json.load(f)
        except OSError:
            _prompts = {}
    return _prompts


def load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        return {"version": 1, "cards": {}, "entries": {}, "reviews": {}, "lessons": [], "slate": {"notes": {}, "predictions": {}, "picks": {}}}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", required=True)
    ap.add_argument("--card")
    ap.add_argument("--items", default="", help="comma-separated item ids (default: every eligible item)")
    ap.add_argument("--review")
    ap.add_argument("--auto", action="store_true")
    ap.add_argument("--patch", help="where to write the patch")
    ap.add_argument("--apply", help="merge this patch into --state")
    ap.add_argument("--status", action="store_true", help="list saved cards and what's missing")
    ap.add_argument("--prompt", metavar="CARD", help="print the whole-card prompt")
    ap.add_argument("--answers", metavar="FILE", help="Claude's JSON answers for --card -> --patch")
    ap.add_argument("--review-prompt", metavar="CARD", help="print the weekly review prompt")
    ap.add_argument("--review-answer", metavar="FILE", help="Claude's review JSON for --card -> --patch")
    args = ap.parse_args(argv)

    if args.status or args.prompt or args.answers or args.review_prompt or args.review_answer:
        state = load_state(args.state)
        if args.status:
            print(status_report(state))
            return
        if args.prompt:
            ids = [int(x) for x in re.split(r"[,\s]+", args.items) if x.strip().isdigit()]
            print(card_prompt(state, args.prompt, ids or None))
            return
        if args.review_prompt:
            print(review_prompt_text(state, args.review_prompt))
            return
        if not args.card:
            sys.exit("--card is required with --answers / --review-answer")
        with open(args.answers or args.review_answer, encoding="utf-8") as f:
            data = manual.extract_json(f.read())
        if args.answers:
            patch = answers_patch(state, args.card, data)
        else:
            patch = review_card(state, args.card, ask_review=lambda *a: data)
        for c in patch.get("cards", {}).values():
            for f in ("claude_job", "review_job"):
                if f in c:
                    print(c[f]["message"])
        with open(args.patch or "patch.json", "w", encoding="utf-8") as f:
            json.dump(patch, f, ensure_ascii=False)
        return

    if args.apply:
        with open(args.apply, encoding="utf-8") as f:
            patch = json.load(f)
        if not any(patch.get(k) for k in ("cards", "reviews", "lessons_replace")):
            print("Nothing to apply.")
            return
        state = apply_patch(load_state(args.state), patch)
        with open(args.state, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, separators=(",", ":"))
        print("Applied.")
        return

    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set (GitHub: Settings > Secrets and variables > Actions).")
    state = load_state(args.state)
    patches = []
    if args.card:
        ids = [int(x) for x in re.split(r"[,\s]+", args.items) if x.strip().isdigit()]
        patches.append(predict_card(state, args.card, ids or None))
    if args.review:
        patches.append(review_card(state, args.review))
    if args.auto:
        to_predict, to_review = due(state)
        print(f"Due: predict {to_predict or 'nothing'}, review {to_review or 'nothing'}")
        for k in to_predict:
            patches.append(predict_card(state, k))
        for k in to_review:
            try:
                patches.append(review_card(state, k))
            except SystemExit as e:
                print(e)
    patch = merge_patches(patches)
    for k, c in patch["cards"].items():
        for f in ("claude_job", "review_job"):
            if f in c:
                print(f"{k}: {c[f]['message']}")
    with open(args.patch or "patch.json", "w", encoding="utf-8") as f:
        json.dump(patch, f, ensure_ascii=False)


if __name__ == "__main__":
    main()
