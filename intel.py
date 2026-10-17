"""Program intel for one team via Claude + web search: coaching staff (with career stops and
play-callers), hot seat, coaching rumors and changes, NIL budget estimates, roster news.

Cutoff-aware: Claude is told the cutoff, and every dated item after it is dropped here as well, so
a replay of a past week can't see later news. Undated items are kept but flagged.
"""
import json
import os
import re
from datetime import datetime, timezone

import anthropic

MODEL = os.getenv("PICKS_CLAUDE_MODEL", "claude-opus-5-5")
MAX_CONTINUATIONS = 4

SYSTEM = """You research one college football program for a handicapper. Use web search.

CUTOFF: only use information published on or before the cutoff date/time given. Ignore and do not
mention anything published after it. Record the publication date for every item.

Reply with ONLY one JSON object (no prose) with these keys:
{
 "head_coach": {"name": str, "since": int (first season as HC here), "note": str},
 "staff": [ {"name": str, "role": str (e.g. "Offensive Coordinator", "Offensive Line Coach"),
             "play_caller": bool, "since": int (first season in this role here),
             "career": [ {"team": str, "role": str, "start": int, "end": int or null} ] } ],
 "hot_seat": {"status": "none" | "warm" | "hot" | "very hot" | "fired/interim", "evidence": str,
              "date": "YYYY-MM-DD", "source": url},
 "rumors": [ {"text": str, "date": "YYYY-MM-DD", "source": url} ],
 "coaching_changes": [ {"text": str, "date": "YYYY-MM-DD", "source": url} ],
 "nil": {"estimate": str (e.g. "$28M-$34M roster budget"), "date": "YYYY-MM-DD", "source": url, "note": str},
 "roster_notes": [ {"text": str, "date": "YYYY-MM-DD", "source": url} ]
}
Staff: head coach's coordinators (offense, defense, special teams) and position coaches (QB, RB,
WR, TE, OL, DL, LB, DB/secondary) when you can find them. Career: their prior college stops with
years, most recent last; use exact school names. Hot seat: use expert hot-seat ratings or rankings
(e.g. CBS Sports, USA Today) and credible reporting; "none" if there is no such talk. NIL: roster
budget estimates are not disclosed by schools, so give the reported range and say it is an
estimate. Leave a list empty rather than guessing."""


def _parse_date(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s)[:10]).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _parse_json(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def enforce_cutoff(data, cutoff):
    """Drop dated items published after the cutoff; count them."""
    dropped = 0
    cut = cutoff.date()

    def check(item):
        d = _parse_date(item.get("date"))
        if d is None:
            item["undated"] = True
        elif d.date() == cut:
            item["same_day_as_cutoff"] = True   # date-only: might be after the cutoff hour
        return d is None or d.date() <= cut

    for key in ("rumors", "coaching_changes", "roster_notes"):
        items = data.get(key) or []
        kept = [i for i in items if isinstance(i, dict) and check(i)]
        dropped += len(items) - len(kept)
        data[key] = kept
    for key in ("hot_seat", "nil"):
        item = data.get(key)
        if isinstance(item, dict) and not check(item):
            data[key] = None
            dropped += 1
    data["excluded_after_cutoff"] = dropped
    return data


def fetch(team, season, cutoff=None):
    cutoff = cutoff or datetime.now(timezone.utc)
    client = anthropic.Anthropic()
    user = (f"Program: {team} football, {season} season. CUTOFF: {cutoff.strftime('%Y-%m-%d %H:%M UTC')}. "
            "Research the coaching staff, hot seat, coaching rumors/changes, NIL budget estimates and roster news.")
    messages = [{"role": "user", "content": user}]
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 10}]
    for _ in range(MAX_CONTINUATIONS + 1):
        resp = client.beta.messages.create(
            model=MODEL, max_tokens=16000, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            output_config={"effort": "medium"}, system=SYSTEM, tools=tools, messages=messages)
        if resp.stop_reason != "pause_turn":
            break
        messages.append({"role": "assistant", "content": resp.content})
    else:
        raise RuntimeError("Program intel search didn't finish; try again.")
    if resp.stop_reason == "refusal":
        raise RuntimeError("Claude declined the program intel search.")
    blocks = list(resp.content)
    last_search = max((i for i, b in enumerate(blocks) if b.type.endswith("tool_result")), default=-1)
    text = "".join(b.text for b in blocks[last_search + 1:] if b.type == "text")
    data = _parse_json(text)
    if data is None:
        raise RuntimeError("Program intel came back in an unexpected format; try again.")
    data = enforce_cutoff(data, cutoff)
    data["team"], data["season"], data["cutoff"] = team, season, cutoff.isoformat(timespec="minutes")
    return data


def describe(data, graded_staff_text=None):
    if not data:
        return "  (no program intel gathered)"
    L = []
    hc = data.get("head_coach") or {}
    if hc.get("name"):
        L.append(f"  Head coach: {hc['name']}" + (f" (since {hc['since']})" if hc.get("since") else "") +
                 (f" - {hc['note']}" if hc.get("note") else ""))
    hs = data.get("hot_seat")
    if hs:
        L.append(f"  Hot seat: {hs.get('status')}" + (f" - {hs.get('evidence')}" if hs.get("evidence") else "") +
                 (f" ({hs.get('date')})" if hs.get("date") else ""))
    for key, label in (("coaching_changes", "Coaching change"), ("rumors", "Rumor"), ("roster_notes", "Roster")):
        for item in (data.get(key) or [])[:4]:
            L.append(f"  {label}: {item.get('text')}" + (f" ({item.get('date')})" if item.get("date") else " (undated)"))
    nil = data.get("nil")
    if nil and nil.get("estimate"):
        L.append(f"  NIL/roster budget (estimate): {nil['estimate']}" + (f" - {nil['note']}" if nil.get("note") else ""))
    if graded_staff_text:
        L.append("  Staff and their units' track records (FBS percentiles):")
        L.append(graded_staff_text)
    if data.get("excluded_after_cutoff"):
        L.append(f"  ({data['excluded_after_cutoff']} item(s) dated after the cutoff were excluded)")
    return "\n".join(L)
