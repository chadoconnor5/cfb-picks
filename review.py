"""Weekly self-review: grade Claude's pool picks against what happened, find where it fell short,
and turn that into lessons and calibration that feed the next week's predictions.

Two layers:
- stats(): deterministic numbers (records by type, confidence calibration, favorite/underdog,
  closing-line value, market-move agreement, Claude vs me). No model involved.
- claude_review(): Claude looks at each prediction next to the box score and classifies the miss
  (right read / lucky / unlucky / wrong read + cause) and proposes concrete lessons.

Lessons are stored per week. Prompts only ever see lessons (and calibration) from EARLIER weeks, so
replaying a past week stays blind.
"""
import json
import os
from collections import defaultdict


MODEL = os.getenv("PICKS_CLAUDE_MODEL", "claude-opus-5-5")
BUCKETS = [(50, 54), (55, 59), (60, 64), (65, 69), (70, 95)]
MISS_CATEGORIES = ["none", "injury_info", "weather", "matchup_style", "market_signal_ignored", "pool_line_value",
                   "turnover_variance", "late_game_variance", "overconfidence", "stale_or_missing_info",
                   "prop_interpretation", "other"]


def _rec(results):
    w = sum(1 for r in results if r == "W")
    l = sum(1 for r in results if r == "L")
    p = sum(1 for r in results if r == "P")
    return {"w": w, "l": l, "p": p, "pct": round(100 * w / (w + l), 1) if w + l else None}


def week_items(items):
    """Flatten a graded card into one row per Claude pick (ATS / O/U / prop)."""
    rows = []
    for it in items:
        c, d = it.get("claude") or {}, it["data"]
        if not c:
            continue
        g = it.get("game") or {}
        if it["kind"] == "prop":
            rows.append({"idx": it["idx"], "type": "prop", "pick": c.get("pick"), "confidence": c.get("confidence"),
                         "result": it.get("claude_prop_result"), "mine": it.get("prop_result"),
                         "line": d.get("line"), "actual": it.get("prop_actual"), "question": d.get("question"),
                         "projection": c.get("projection"), "reasoning": c.get("reasoning"), "audit": c.get("audit")})
            continue
        base = {"idx": it["idx"], "game": f"{d['away']} @ {d['home']}", "weight": d.get("weight", 1),
                "pool_spread": d["home_spread"], "pool_total": d.get("total"), "confidence": c.get("confidence"),
                "proj": f"{d['away']} {c.get('proj_away')}-{c.get('proj_home')} {d['home']}",
                "final": f"{d['away']} {g.get('away_points')}-{g.get('home_points')} {d['home']}" if g.get("completed") else None,
                "reasoning": c.get("reasoning"), "key_factors": c.get("key_factors"),
                "conditions_impact": c.get("conditions_impact"), "audit": c.get("audit"),
                "market": it.get("market"), "actual": d.get("actual"), "flipped": d.get("flipped")}
        if c.get("ats_pick"):
            rows.append({**base, "type": "mid_major" if it["kind"] == "mid_major" else "ats", "pick": c["ats_pick"],
                         "result": it.get("claude_ats_result"), "mine": it.get("ats_result"), "my_pick": it.get("my_ats")})
        if c.get("ou_pick"):
            rows.append({**base, "type": "ou", "pick": c["ou_pick"], "result": it.get("claude_ou_result"),
                         "mine": it.get("ou_result"), "my_pick": it.get("my_ou")})
    return rows


def _clv(row):
    """Did the closing market move toward Claude's side vs the pool line? (+ = beat the close)"""
    m = row.get("market") or {}
    if row["type"] in ("ats", "mid_major") and m.get("home_spread") is not None:
        diff = row["pool_spread"] - m["home_spread"]      # + = pool gives the home side more points than the close
        return diff if row["pick"] == "home" else -diff
    if row["type"] == "ou" and m.get("total") is not None and row.get("pool_total") is not None:
        diff = m["total"] - row["pool_total"]             # + = market total moved above the pool total
        return diff if row["pick"] == "over" else -diff
    return None


def stats(rows):
    graded = [r for r in rows if r.get("result") in ("W", "L", "P")]
    out = {"n": len(graded), "overall": _rec([r["result"] for r in graded])}
    by_type = defaultdict(list)
    for r in graded:
        by_type[r["type"]].append(r["result"])
    out["by_type"] = {k: _rec(v) for k, v in by_type.items()}
    out["calibration"] = []
    for lo, hi in BUCKETS:
        sub = [r for r in graded if r.get("confidence") is not None and lo <= r["confidence"] <= hi]
        if sub:
            rec = _rec([r["result"] for r in sub])
            out["calibration"].append({"range": f"{lo}-{hi}", "expected": round((lo + hi) / 2), **rec})
    ats = [r for r in graded if r["type"] in ("ats", "mid_major")]
    fav = [r for r in ats if (r["pick"] == "home") == (r["pool_spread"] < 0)]
    dog = [r for r in ats if (r["pick"] == "home") == (r["pool_spread"] > 0)]
    out["favorites"], out["underdogs"] = _rec([r["result"] for r in fav]), _rec([r["result"] for r in dog])
    clv = [(r, _clv(r)) for r in graded]
    clv = [(r, v) for r, v in clv if v is not None]
    if clv:
        out["closing_line_value"] = {"avg_points": round(sum(v for _, v in clv) / len(clv), 2),
                                     "beat_close_pct": round(100 * sum(1 for _, v in clv if v > 0) / len(clv), 1),
                                     "with_market_move": _rec([r["result"] for r, v in clv if v > 0]),
                                     "against_market_move": _rec([r["result"] for r, v in clv if v < 0])}
    mine = [r for r in graded if r.get("mine") in ("W", "L", "P")]
    out["me"] = _rec([r["mine"] for r in mine])
    agree = [r for r in mine if r.get("my_pick") and r["my_pick"] == r["pick"]]
    out["agreement"] = {"n": len(agree), "of": len([r for r in mine if r.get("my_pick")]),
                        "when_agree": _rec([r["result"] for r in agree]),
                        "claude_when_disagree": _rec([r["result"] for r in mine if r.get("my_pick") and r["my_pick"] != r["pick"]]),
                        "me_when_disagree": _rec([r["mine"] for r in mine if r.get("my_pick") and r["my_pick"] != r["pick"]])}
    return out


REVIEW_SYSTEM = """You are reviewing your own college football pool picks after the games, to get better next week.
For each pick you get: what you predicted and why, the information cutoff and what you had, the final score,
box-score stats, the pool line and the closing market line.

Be honest and specific. Separate process from outcome:
- correct_read: right pick for the right reasons.
- lucky: won, but your reasons didn't play out (or the result hinged on a fluke).
- unlucky: lost despite a sound read (e.g. late backdoor cover, fluky turnovers, beat the closing line).
- wrong_read: your analysis was wrong; name exactly what you misjudged.
Use miss_category to tag the main cause (use "none" for correct reads). Lessons must be concrete and reusable
next week ("weight X more when Y", "don't fade a market move of 2+ points toward the favorite without Z"),
not generic. Propose at most 5 lessons, each tied to evidence from this week; skip lessons supported by one fluky game."""


def _schema(n_items):
    return {
        "type": "object", "additionalProperties": False, "required": ["items", "summary", "lessons", "calibration_note"],
        "properties": {
            "items": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["idx", "type", "verdict", "miss_category", "what_happened", "lesson"],
                "properties": {"idx": {"type": "integer"}, "type": {"type": "string"},
                               "verdict": {"type": "string", "enum": ["correct_read", "lucky", "unlucky", "wrong_read"]},
                               "miss_category": {"type": "string", "enum": MISS_CATEGORIES},
                               "what_happened": {"type": "string"}, "lesson": {"type": "string"}}}},
            "summary": {"type": "string"},
            "lessons": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["text", "applies_to", "evidence"],
                "properties": {"text": {"type": "string"}, "applies_to": {"type": "string", "enum": ["ats", "ou", "props", "all"]},
                               "evidence": {"type": "string"}}}},
            "calibration_note": {"type": "string"}}}


def claude_review(rows, week_stats, season, week, prior_lessons=None):
    import anthropic
    client = anthropic.Anthropic()
    payload = {"season": season, "week": week, "stats": week_stats, "picks": rows,
               "lessons_you_were_using": prior_lessons or []}
    resp = client.beta.messages.create(
        model=MODEL, max_tokens=16000, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": _schema(len(rows))}},
        system=REVIEW_SYSTEM, messages=[{"role": "user", "content": json.dumps(payload, default=str)}])
    if resp.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"Review stopped: {resp.stop_reason}")
    return finalize(json.loads(next(b.text for b in resp.content if b.type == "text")))


def finalize(out):
    """Validate a review answer (from the API or pasted from a chat)."""
    if not isinstance(out, dict):
        raise ValueError("The review answer should be one JSON object.")
    missing = [k for k in ("items", "summary", "lessons") if k not in out]
    if missing:
        raise ValueError(f"Review answer is missing: {', '.join(missing)}")
    lessons = []
    for l in out.get("lessons") or []:
        if isinstance(l, dict) and str(l.get("text") or "").strip():
            at = l.get("applies_to") if l.get("applies_to") in ("ats", "ou", "props", "all") else "all"
            lessons.append({"text": str(l["text"]).strip(), "applies_to": at, "evidence": str(l.get("evidence") or "")})
    out["lessons"] = lessons[:5]
    out.setdefault("calibration_note", "")
    return out


def calibration_lines(season_stats):
    """Short calibration facts for the prompt."""
    lines = []
    for b in season_stats.get("calibration", []):
        if b["w"] + b["l"] >= 5:
            lines.append(f"Your {b['range']} confidence picks have won {b['pct']}% ({b['w']}-{b['l']}); "
                         f"{'you are overconfident there' if b['pct'] is not None and b['pct'] < b['expected'] - 8 else 'roughly calibrated' if b['pct'] is not None and abs(b['pct'] - b['expected']) <= 8 else 'you can be more confident there'}.")
    clv = season_stats.get("closing_line_value")
    if clv:
        lines.append(f"You beat the closing line on {clv['beat_close_pct']}% of picks (avg {clv['avg_points']:+} pts); "
                     f"picks with the market move went {clv['with_market_move']['w']}-{clv['with_market_move']['l']}, "
                     f"against it {clv['against_market_move']['w']}-{clv['against_market_move']['l']}.")
    for k, label in (("ats", "spread"), ("ou", "over/under"), ("prop", "prop")):
        r = season_stats.get("by_type", {}).get(k)
        if r and r["w"] + r["l"] >= 5:
            lines.append(f"Season {label} record: {r['w']}-{r['l']} ({r['pct']}%).")
    return lines
