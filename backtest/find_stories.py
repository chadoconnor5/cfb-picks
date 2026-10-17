"""Grow the stories set: for the most extreme-weather games the data found, have Claude search
for game recaps that mention the weather and extract what reporters said.

    python -m backtest.find_stories --top 30

Reads backtest/results.json (from backtest.analyze) and writes backtest/stories_found.json for you to
review. Copy the entries you trust into stories.json, then re-run backtest.analyze.

Cost: one Claude call with up to 5 web searches per game.
"""
import argparse
import json
import os
import re
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import anthropic  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.getenv("PICKS_CLAUDE_MODEL", "claude-opus-5-5")

SYSTEM = """You verify how weather affected a past college football game, using web search.
Find game recaps or beat-reporter coverage of the specific game. Report only what sources say.

Reply with ONLY a JSON object, no prose, with these keys:
  "weather_mentioned": true/false (did coverage say weather affected the game?)
  "reported": short description of conditions as reported (wind, rain, snow, cold), or ""
  "reported_impact": 1-2 sentences on how coverage says weather changed play (passing, kicking,
                     turnovers, play-calling), with any stats quoted, or ""
  "sources": list of URLs you used
If you can't find coverage of this game, return weather_mentioned false and empty fields."""


def _parse_json(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def search_game(client, g):
    w = g["weather"]
    user = (f"Game: {g['matchup']} on {g['date']} (season {g['season']}, week {g['week']}). Final {g['score']} (away-home). "
            f"Historical weather data says about {w['temp_f']}F, wind {w['wind_mph']} mph (gusts {w['gust_mph']}), "
            f"precipitation {w['precip_in']} in, snow {w['snow_in']} in. Did game coverage describe weather affecting play?")
    messages = [{"role": "user", "content": user}]
    for _ in range(4):
        resp = client.beta.messages.create(
            model=MODEL, max_tokens=8000, betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            output_config={"effort": "low"}, system=SYSTEM, messages=messages,
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 5}],
        )
        if resp.stop_reason != "pause_turn":
            break
        messages.append({"role": "assistant", "content": resp.content})
    text = "".join(b.text for b in resp.content if b.type == "text")
    return _parse_json(text)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args(argv)
    with open(os.path.join(HERE, "results.json")) as f:
        res = json.load(f)
    known = {(s["story"]["season"], frozenset((s["story"]["home"].lower(), s["story"]["away"].lower())))
             for s in res["stories"]}
    client = anthropic.Anthropic()
    found = []
    for g in res["extreme_games"][:args.top]:
        away, home = g["matchup"].split(" at ", 1)
        if (g["season"], frozenset((home.lower(), away.lower()))) in known:
            continue
        print(f"Searching: {g['matchup']} {g['date']}…")
        try:
            r = search_game(client, g)
        except Exception as e:
            print(f"  failed: {e}")
            continue
        if r and r.get("weather_mentioned"):
            found.append({"season": g["season"], "date": g["date"], "away": away, "home": home,
                          "reported": r.get("reported", ""), "reported_impact": r.get("reported_impact", ""),
                          "expected": "", "sources": r.get("sources", []), "found_by": "find_stories"})
            print(f"  ✓ {r.get('reported')}")
        else:
            print("  no weather coverage found")
    out = os.path.join(HERE, "stories_found.json")
    with open(out, "w") as f:
        json.dump(found, f, indent=2)
    print(f"{len(found)} stories written to {out}. Review, then copy good ones into stories.json.")


if __name__ == "__main__":
    main()
