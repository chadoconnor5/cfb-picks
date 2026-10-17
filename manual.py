"""Copy-for-Claude mode: no Anthropic API key needed.

The app builds the full prompt (instructions, all the data it gathered, and the exact JSON to answer
with). You paste it into a Claude chat (claude.ai, with web search on so Claude can look up injuries
and coaching news itself), copy Claude's answer back, and the app validates and stores it exactly as
if it had come from the API.

Mode: PICKS_CLAUDE_MODE=manual|api. Default is api when ANTHROPIC_API_KEY is set, else manual.
"""
import json
import os
import re

import predict

MANUAL_MODEL = "claude (chat)"


def mode():
    m = (os.getenv("PICKS_CLAUDE_MODE") or "").strip().lower()
    if m in ("manual", "api"):
        return m
    return "api" if os.getenv("ANTHROPIC_API_KEY", "").strip() else "manual"


def extract_json(text):
    """Pull the JSON out of a pasted chat answer (code fences, prose before/after are fine)."""
    if not text or not text.strip():
        raise ValueError("Paste Claude's answer first.")
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    candidates = ([fence.group(1)] if fence else []) + [t]
    dec = json.JSONDecoder()
    for c in candidates:
        c = c.replace("“", '"').replace("”", '"')  # smart quotes from some editors
        for i, ch in enumerate(c):
            if ch in "[{":
                try:
                    return dec.raw_decode(c[i:])[0]
                except json.JSONDecodeError:
                    continue
    raise ValueError("Couldn't find JSON in the answer. Copy Claude's whole reply (or just the JSON block).")


def research_block(teams, as_of, replay):
    teams = sorted(set(teams))
    lines = ["RESEARCH FIRST (use web search):",
             f"- Injury / availability news for: {', '.join(teams)} (QB, skill players, OL, key defenders; "
             "practice reports, coach comments, opt-outs, suspensions).",
             "- Coaching news for those programs: staff changes, interim coaches, hot-seat talk, rumors, "
             "and anything that changes motivation.",
             "- Anything else the data below can't know (QB changes, weather updates, travel issues)."]
    if replay:
        lines += [f"- STRICT CUTOFF: {as_of}. This is a blind replay. Use ONLY sources published before the cutoff "
                  "and ignore anything you know about these games' results or later news. If a source isn't dated "
                  "before the cutoff, don't use it."]
    else:
        lines += ["- Prefer the newest reports and say when you couldn't find anything."]
    lines += ["Note the most important findings in reasoning / key_factors."]
    return "\n".join(lines)


GAME_SPEC = """{"item": <item number>, "ats_pick": "<exact team name or home/away>", "ou_pick": "over" | "under" | null,
 "projected_home_score": <int>, "projected_away_score": <int>, "confidence": <50-95>,
 "reasoning": "<2-3 sentences>", "key_factors": ["<up to 3>"], "conditions_impact": "<1 sentence>"}"""

PROP_SPEC = """{"item": <item number>, "pick": "over" | "under", "projection": <number>, "confidence": <50-95>,
 "reasoning": "<2 sentences>"}"""


def card_prompt(sections, as_of, replay, title):
    """sections: [{"idx", "kind", "teams", "text"}]. One prompt for a whole pool card (or part of it)."""
    teams = [t for s in sections for t in s["teams"]]
    kinds = {s["kind"] for s in sections}
    out = [f"# {title}", "", "## How to pick", predict.SYSTEM]
    if "prop" in kinds:
        out += ["", "For the prop questions:", predict.PROP_SYSTEM]
    out += ["", research_block(teams, as_of, replay), "", "## Items"]
    for s in sections:
        label = {"game": "Game", "mid_major": "Mid-major game (spread only, no total)", "prop": "Question"}[s["kind"]]
        out += ["", f"### Item {s['idx']} - {label}", s["text"]]
    out += ["", "## Answer format",
            "Reply with ONE JSON array in a ```json code block, one object per item, nothing else inside the block.",
            "Game items:", GAME_SPEC]
    if "prop" in kinds:
        out += ["Question items:", PROP_SPEC]
    out += ["For a game with no total, use \"ou_pick\": null. Pick against the line shown for each item."]
    return "\n".join(out)


def game_prompt(game, notes, context):
    """Prompt for one slate game (Slate tab)."""
    return card_prompt([{"idx": 1, "kind": "game", "teams": [game["home_team"], game["away_team"]],
                         "text": predict.build_prompt(game, notes, context)}],
                       context.get("as_of"), context.get("replay"), f"{game['away_team']} at {game['home_team']}")


def answers_by_item(data):
    """Normalize a pasted answer to {item_number: obj}. Accepts an array, {"items": [...]}, or one object."""
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        data = data["items"]
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ValueError("Expected a JSON array of answers.")
    out = {}
    for i, obj in enumerate(data, 1):
        if not isinstance(obj, dict):
            raise ValueError(f"Answer #{i} isn't a JSON object.")
        n = obj.get("item", obj.get("idx"))
        try:
            n = int(n) if n is not None else (i if len(data) > 1 else None)
        except (TypeError, ValueError):
            raise ValueError(f"Answer #{i} has a bad item number: {n!r}")
        out[n] = obj
    return out


def review_prompt(rows, week_stats, season, week, prior_lessons, system, schema):
    payload = {"season": season, "week": week, "stats": week_stats, "picks": rows,
               "lessons_you_were_using": prior_lessons or []}
    return "\n".join([
        f"# Self-review of my week {week} pool picks ({season})", "", system, "",
        "Here is the data (JSON):", "```json", json.dumps(payload, default=str, indent=1), "```", "",
        "## Answer format",
        "Reply with ONE JSON object in a ```json code block that matches this JSON schema:",
        "```json", json.dumps(schema, indent=1), "```"])
