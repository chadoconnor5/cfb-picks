"""Ask Claude for an ATS / O/U prediction for one game, as structured JSON."""
import json
import os

import anthropic

import analytics
import conditions
from grading import format_spread

MODEL = os.getenv("PICKS_CLAUDE_MODEL", "claude-opus-5-5")
EFFORT = os.getenv("PICKS_CLAUDE_EFFORT", "high")  # low | medium | high | xhigh | max

SYSTEM = """You are a sharp, disciplined college football handicapper. For the game you are given,
pick a side against the spread and the over/under, and project the final score.

You may be given current-season analytics, a kickoff forecast with a style-vs-weather assessment,
and an injury report. Treat them as more current than your own knowledge.

How to use them:
- Start from the analytics (EPA per play, success rate, SP+) and how they compare with the market
  line. The line already prices in the obvious stuff, so look for what it might be missing.
- Weather: ask how each team's style holds up in these conditions. Pass-heavy and explosive-passing
  offenses lose the most in wind, rain and snow, run-first teams with efficient ground games lose
  the least, and a stout run defense blunts a run-first team's insulation. Wind matters more than
  rain, and a mild forecast should change nothing. The assessment is a heuristic, so overrule it
  when you have a reason. When historical evidence from the backtest is included, trust it over
  the rules of thumb, weigh it by sample size, and remember that closing lines already price in
  forecast weather.
- Injuries change style: a pass-heavy team without its QB or top receivers is more fragile, and a
  run team without its line or lead back loses its weather insulation. Combine injuries with weather.
- Weigh the user's notes heavily. If there is little current data for a team, say so and lower confidence.
- confidence is an integer from 50 to 95: 50 = coin flip, 95 = as sure as a pick ever gets.
  Most picks should land between 52 and 65.
- Keep the projected score consistent with your picks: the projected margin should be on the
  side of the spread you picked, and the projected total on the side of the total you picked.
- reasoning is 2-3 sentences. key_factors has at most 3 short items.
- conditions_impact is one sentence on how weather and injuries affect this game given each team's
  style, or "Not a factor." when they don't."""


def _schema(game):
    return {
        "type": "object",
        "properties": {
            "ats_pick": {"type": "string", "enum": [game["home_team"], game["away_team"]]},
            "ou_pick": {"type": "string", "enum": ["over", "under"]},
            "projected_home_score": {"type": "integer"},
            "projected_away_score": {"type": "integer"},
            "confidence": {"type": "integer"},
            "reasoning": {"type": "string"},
            "key_factors": {"type": "array", "items": {"type": "string"}},
            "conditions_impact": {"type": "string"},
        },
        "required": ["ats_pick", "ou_pick", "projected_home_score", "projected_away_score",
                     "confidence", "reasoning", "key_factors", "conditions_impact"],
        "additionalProperties": False,
    }


def header_lines(game, context=None):
    """Matchup, kickoff and the line being picked against (the parts that depend on the pool's line)."""
    context = context or {}
    home, away = game["home_team"], game["away_team"]
    spread, total = game.get("spread"), game.get("total")
    site = "Neutral site" if game.get("neutral_site") else f"at {home}"
    lines = [
        f"Matchup: {away} ({game.get('away_conf') or '?'}) vs {home} ({game.get('home_conf') or '?'})",
        f"Season {game['season']}, week {game['week']} ({game['season_type']}). {site}"
        + (f", {game['venue']}" if game.get("venue") else "") + ".",
        f"Kickoff: {game.get('start_date') or 'TBD'}",
        f"Spread: {format_spread(home, spread)} / {format_spread(away, -spread if spread is not None else None)}",
        f"Total: {total if total is not None else 'no line'}",
    ]
    m = context.get("market_now")
    if m:
        label = m.get("label") or "current sportsbook line"
        lines.append(f"This is a pick'em pool line set earlier in the week. Market ({label}): "
                     f"{format_spread(m['home'], m['spread'])}, total {m['total'] if m['total'] is not None else 'n/a'}. "
                     "Pick against the pool line above; use any gap to the market as information.")
    if game.get("spread_open") is not None or game.get("total_open") is not None:
        lines.append(f"Opening line: {format_spread(home, game.get('spread_open'))}, total {game.get('total_open')}")
    return lines


def context_lines(game, context=None, with_injuries=True):
    """Everything known about the game beyond the line (no notes, no lessons)."""
    context = context or {}
    home, away = game["home_team"], game["away_team"]
    lines = []
    for team, note in (context.get("program_notes") or {}).items():
        lines.append(f"Program note - {team}: {note}")
    lines += ["", f"Team analytics, {game['season']} season (FBS percentiles; defense pct: higher = stingier):",
              f"{away}:", analytics.describe(context.get("away_profile")),
              f"{home}:", analytics.describe(context.get("home_profile"))]
    if context.get("assessment"):
        lines += ["", "Weather and style assessment (heuristic):", conditions.describe(context["assessment"])]
    if context.get("history"):
        import history
        lines += ["", "Betting history (CFBD consensus closing lines):", history.describe(context["history"], home, away)]
    if context.get("situation"):
        import situational
        lines += ["", "Situation (travel, rest, schedule spots):", situational.describe(context["situation"])]
    if context.get("hfa_text"):
        lines += ["", "Home field and crowd (vs closing spreads, completed seasons):", context["hfa_text"]]
    if context.get("coaching"):
        import coaches
        lines += ["", "Coaching (records from data; ATS = vs closing spread):"]
        lines += [coaches.describe(context["coaching"][t]) for t in (away, home) if context["coaching"].get(t)]
    if context.get("intel"):
        import intel
        lines += ["", "Program intel (dated, sourced; cutoff enforced):"]
        for t in (away, home):
            if context["intel"].get(t):
                lines += [f" {t}:", intel.describe(context["intel"][t], (context.get("staff_text") or {}).get(t))]
    if context.get("fcs"):
        import fcs
        for t, h in context["fcs"].items():
            if h and "error" not in h:
                lines += ["", f"FCS history ({t} is new to FBS):", fcs.describe(t, h)]
    if context.get("replay"):
        lines += ["", f"Information cutoff: {context['as_of']}. Everything here was limited to that time; "
                      "do not use any knowledge of later events."]
    if with_injuries:
        inj = context.get("injuries")
        lines += ["", "Injury / availability report" + (f" (gathered {inj['fetched_at']} UTC)" if inj else "") + ":",
                  inj["report"] if inj else "(not gathered; research it or rely on the notes)"]
    return lines


def build_prompt(game, notes, context=None):
    """context: {"home_profile", "away_profile", "assessment", "injuries", ...} (all optional)."""
    context = context or {}
    lines = header_lines(game, context) + context_lines(game, context)
    if context.get("lessons"):
        lines += ["", "Lessons from grading your own past picks (apply them):"] + [f"  - {x}" for x in context["lessons"]]
    lines += ["", f"My notes: {notes.strip() if notes and notes.strip() else '(none)'}"]
    return "\n".join(lines)


def predict(game, notes="", context=None):
    """Return a dict ready to insert into the predictions table."""
    client = anthropic.Anthropic()
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": _schema(game)}},
        system=SYSTEM,
        messages=[{"role": "user", "content": build_prompt(game, notes, context)}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined to make this prediction.")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Claude ran out of tokens before finishing; try again.")
    text = next(b.text for b in response.content if b.type == "text")
    return row_from_data(game, json.loads(text), notes, context, response.model)


def row_from_data(game, data, notes="", context=None, model="claude"):
    """Validated prediction row from Claude's JSON (API or pasted from a chat)."""
    missing = [k for k in ("ats_pick", "ou_pick", "projected_home_score", "projected_away_score", "confidence",
                           "reasoning") if k not in data]
    if missing:
        raise ValueError(f"Answer is missing: {', '.join(missing)}")
    pick = str(data["ats_pick"]).strip()
    if pick.lower() in (game["home_team"].lower(), "home"):
        side = "home"
    elif pick.lower() in (game["away_team"].lower(), "away"):
        side = "away"
    else:
        raise ValueError(f"ats_pick '{pick}' is neither {game['away_team']} nor {game['home_team']}")
    ou = str(data["ou_pick"]).strip().lower() if data.get("ou_pick") else None
    if ou not in ("over", "under", None):
        raise ValueError(f"ou_pick must be over or under, got '{data['ou_pick']}'")
    return {
        "game_id": game["id"],
        "model": model,
        "spread": game.get("spread"),
        "total": game.get("total"),
        "notes": notes or "",
        "ats_pick": side,
        "ou_pick": ou,
        "proj_home": int(round(float(data["projected_home_score"]))),
        "proj_away": int(round(float(data["projected_away_score"]))),
        "confidence": max(50, min(95, int(round(float(data["confidence"]))))),
        "reasoning": str(data["reasoning"]).strip(),
        "key_factors": json.dumps([str(f).strip() for f in (data.get("key_factors") or []) if str(f).strip()][:3]),
        "conditions_impact": str(data.get("conditions_impact") or "").strip(),
        "context": json.dumps(context or {}),
    }


PROP_SYSTEM = """You are a sharp college football handicapper answering a pick'em pool's over/under
prop question. Use the per-game stat history (for and against), the matchup, the game line and
total (game script), weather, and injuries. Project the stat, then pick over or under the line.
confidence is 50-95 (most props 52-65). reasoning is 2 sentences."""

STAT_LABELS = {"rush_yds": "rushing yards", "pass_yds": "passing yards", "total_yds": "total yards",
               "sacks": "sacks", "points": "points", "turnovers": "turnovers"}


def prop_prompt(prop, game, context):
    lines = [f"Question: {prop['question']}", f"Line: {prop['line']}",
             f"Interpreted as: {STAT_LABELS.get(prop.get('stat'), prop.get('stat') or 'unknown stat')} - "
             + ("both teams combined" if prop.get("scope") == "combined" else f"{prop.get('team')} only")
             + (f" (opponent: {prop.get('opponent')})" if prop.get("opponent") else "")]
    if game:
        lines.append(f"Game: {game['away_team']} at {game['home_team']}, line {format_spread(game['home_team'], game.get('spread'))}, "
                     f"total {game.get('total')}, kickoff {game.get('start_date')}")
    m = context.get("history")
    if m:
        for side in ("away", "home"):
            t = m["teams"][side]
            st = t["stats"]
            key = prop.get("stat")
            if key:
                f_, a_ = st["for"].get(key), st["against"].get(key)
                lines.append(f"{t['team']} {STAT_LABELS.get(key, key)} per game this season: "
                             + (f"avg {f_['avg']}, last 3 {f_['last3']}, range {f_['min']}-{f_['max']} (n={f_['n']})" if f_ else "n/a")
                             + "; allowed to opponents: "
                             + (f"avg {a_['avg']}, last 3 {a_['last3']}" if a_ else "n/a"))
            lines.append(f"{t['team']} game log: " + "; ".join(
                f"vs {g['opponent']}: {g.get(key) if key else ''}" for g in st["games"][-6:]))
    if context.get("assessment"):
        lines += ["Weather/style:", conditions.describe(context["assessment"])]
    if context.get("injuries"):
        lines += ["Injuries:", context["injuries"]["report"]]
    return "\n".join(lines)


def predict_prop(prop, game, context):
    client = anthropic.Anthropic()
    schema = {"type": "object", "additionalProperties": False,
              "required": ["pick", "projection", "confidence", "reasoning"],
              "properties": {"pick": {"type": "string", "enum": ["over", "under"]}, "projection": {"type": "number"},
                             "confidence": {"type": "integer"}, "reasoning": {"type": "string"}}}
    response = client.beta.messages.create(
        model=MODEL, max_tokens=8000, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": schema}},
        system=PROP_SYSTEM, messages=[{"role": "user", "content": prop_prompt(prop, game, context)}])
    if response.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"Prop prediction stopped: {response.stop_reason}")
    return prop_from_data(json.loads(next(b.text for b in response.content if b.type == "text")), response.model)


def prop_from_data(data, model="claude"):
    pick = str(data.get("pick", "")).strip().lower()
    if pick not in ("over", "under"):
        raise ValueError(f"pick must be over or under, got '{data.get('pick')}'")
    return {"pick": pick, "projection": float(data.get("projection")) if data.get("projection") is not None else None,
            "confidence": max(50, min(95, int(round(float(data.get("confidence", 55)))))),
            "reasoning": str(data.get("reasoning") or "").strip(), "model": model}
