"""FCS history from ncaa.com via the free NCAA API (https://ncaa-api.henrygd.me), for programs new to
FBS (e.g. North Dakota State and Sacramento State in 2026) whose earlier seasons aren't in CFBD's FBS data.

    python -m fcs summary "North Dakota State" 2025
    python -m fcs discover 2025        # one-time: map NCAA stat ids -> titles (reference/ncaa_stat_ids.json)

The public API allows 5 requests/second; calls are paced and cached (past seasons forever).
"""
import hashlib
import json
import os
import re
import sys
import time

import requests

BASE = os.getenv("NCAA_API_URL", "https://ncaa-api.henrygd.me")
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "backtest", "cache")
STAT_IDS = os.path.join(HERE, "reference", "ncaa_stat_ids.json")
ALIASES = {
    "North Dakota State": ["north-dakota-st", "north dakota st", "north dakota state", "ndsu"],
    "Sacramento State": ["sacramento-st", "sacramento st", "sacramento state", "sac state"],
}
MAX_WEEKS = 16


def _norm(s):
    return re.sub(r"[^a-z0-9 ]", " ", (s or "").lower().replace("-", " ")).split()


def _get(path):
    os.makedirs(CACHE, exist_ok=True)
    fp = os.path.join(CACHE, "ncaa-" + hashlib.sha1(path.encode()).hexdigest()[:16] + ".json")
    if os.path.exists(fp):
        with open(fp) as f:
            return json.load(f)
    resp = requests.get(BASE + path, timeout=30)
    time.sleep(0.25)
    if resp.status_code == 404:
        data = None
    else:
        resp.raise_for_status()
        data = resp.json()
    with open(fp, "w") as f:
        json.dump(data, f)
    return data


def _names(side):
    n = side.get("names") or {}
    return [n.get("short"), n.get("full"), n.get("seo"), n.get("char6")]


def is_team(side, team):
    keys = ALIASES.get(team, [team.lower()])
    for name in _names(side):
        if not name:
            continue
        low = name.lower()
        if any(k == low or k == " ".join(_norm(low)) for k in keys) or " ".join(_norm(low)) in [" ".join(_norm(k)) for k in keys]:
            return True
    return False


def parse_scoreboard(data, team):
    """Games for `team` from one /scoreboard response."""
    out = []
    for wrap in (data or {}).get("games") or []:
        g = wrap.get("game") or wrap
        home, away = g.get("home") or {}, g.get("away") or {}
        for me, opp, site in ((home, away, "home"), (away, home, "away")):
            if is_team(me, team):
                try:
                    pf, pa = int(me.get("score")), int(opp.get("score"))
                except (TypeError, ValueError):
                    continue
                if str(g.get("gameState", "final")).lower() not in ("final", ""):
                    continue
                on = opp.get("names") or {}
                out.append({"date": g.get("startDate"), "site": site, "opponent": on.get("short") or on.get("full"),
                            "opponent_fbs": bool(opp.get("conferences") and any(
                                (c.get("conferenceDivision") or "").lower() == "fbs" for c in opp.get("conferences") or [])),
                            "pf": pf, "pa": pa, "won": pf > pa})
    return out


def season_games(team, season, division="fcs"):
    games = []
    for wk in range(1, MAX_WEEKS + 1):
        data = _get(f"/scoreboard/football/{division}/{season}/{wk:02d}/all-conf")
        games += parse_scoreboard(data, team)
    seen, uniq = set(), []
    for g in games:
        key = (g["date"], g["opponent"])
        if key not in seen:
            seen.add(key)
            uniq.append(g)
    return uniq


def summarize(games):
    if not games:
        return None
    n = len(games)
    w = sum(1 for g in games if g["won"])
    pf = sum(g["pf"] for g in games) / n
    pa = sum(g["pa"] for g in games) / n
    fbs = [g for g in games if g["opponent_fbs"]]
    return {"games": n, "record": f"{w}-{n - w}", "ppg": round(pf, 1), "papg": round(pa, 1), "margin": round(pf - pa, 1),
            "vs_fbs": [f"{'W' if g['won'] else 'L'} {g['pf']}-{g['pa']} vs {g['opponent']}" for g in fbs],
            "results": [f"{'W' if g['won'] else 'L'} {g['pf']}-{g['pa']} {'vs' if g['site'] == 'home' else 'at'} {g['opponent']}"
                        for g in games]}


def team_stats(team, season, division="fcs"):
    """National stat ranks for the team using the discovered stat-id map (if present)."""
    try:
        with open(STAT_IDS) as f:
            ids = json.load(f)
    except OSError:
        return []
    out = []
    for sid, title in ids.items():
        data = _get(f"/stats/football/{division}/{season}/team/{sid}")
        for page_row in (data or {}).get("data") or []:
            name = page_row.get("Team") or page_row.get("Name") or ""
            if any(k in name.lower() for k in ALIASES.get(team, [team.lower()])):
                out.append({"stat": title, "rank": page_row.get("Rank"),
                            "value": {k: v for k, v in page_row.items() if k not in ("Rank", "Team", "Name")}})
    return out


def discover(season, division="fcs", max_id=1000):
    """Find which stat ids exist and what they are; saves reference/ncaa_stat_ids.json."""
    found = {}
    for sid in range(1, max_id + 1):
        data = _get(f"/stats/football/{division}/{season}/team/{sid}")
        if data and data.get("title"):
            found[str(sid)] = data["title"]
            print(sid, data["title"])
    with open(STAT_IDS, "w") as f:
        json.dump(found, f, indent=1)
    return found


def history(team, season, years=2):
    """FCS summaries for the `years` seasons before `season`."""
    out = {}
    for s in range(season - years, season):
        try:
            summ = summarize(season_games(team, s))
        except Exception as e:
            summ = {"error": str(e)}
        if summ:
            summ["stats"] = team_stats(team, s) if "error" not in summ else []
            out[s] = summ
    return out


def describe(team, hist):
    if not hist:
        return f"  {team}: no FCS history found"
    L = []
    for s, h in sorted(hist.items()):
        if "error" in h:
            L.append(f"  {team} {s} (FCS): unavailable ({h['error']})")
            continue
        L.append(f"  {team} {s} (FCS): {h['record']}, {h['ppg']} ppg, {h['papg']} allowed, margin {h['margin']:+}")
        if h["vs_fbs"]:
            L.append(f"    vs FBS: {'; '.join(h['vs_fbs'])}")
        for st in (h.get("stats") or [])[:6]:
            L.append(f"    {st['stat']}: FCS rank {st['rank']}")
    L.append("    (FCS competition: discount these numbers against FBS opponents.)")
    return "\n".join(L)


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "discover":
        discover(int(sys.argv[2]))
    elif len(sys.argv) >= 4 and sys.argv[1] == "summary":
        print(describe(sys.argv[2], history(sys.argv[2], int(sys.argv[3]) + 1, years=1)))
    else:
        print(__doc__)
