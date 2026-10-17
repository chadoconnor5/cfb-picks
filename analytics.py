"""Team style profiles built from CFBD season analytics.

One profile per team per season: how often a team passes, how efficient it is through the air
vs. on the ground, how explosive, how fast it plays, and how its defense holds up against each.
Every key metric also gets an FBS percentile so "pass-heavy" means something relative.
"""
from cfbd import _f, _get, _num


def _pct_rank(values, v, higher_is_better=True):
    """Percentile (0-100) of v within values."""
    vals = [x for x in values if x is not None]
    if v is None or not vals:
        return None
    below = sum(1 for x in vals if x < v)
    equal = sum(1 for x in vals if x == v)
    pct = 100 * (below + 0.5 * equal) / len(vals)
    return round(pct if higher_is_better else 100 - pct)


def _side(adv, side):
    s = adv.get(side) or {}
    pp = _f(s, "passingPlays", "passing_plays") or {}
    rp = _f(s, "rushingPlays", "rushing_plays") or {}
    havoc = s.get("havoc") or {}
    return {
        "plays": _num(s.get("plays")),
        "ppa": _num(s.get("ppa")),
        "success_rate": _num(_f(s, "successRate", "success_rate")),
        "explosiveness": _num(s.get("explosiveness")),
        "pass_rate": _num(pp.get("rate")),
        "pass_ppa": _num(pp.get("ppa")),
        "pass_sr": _num(_f(pp, "successRate", "success_rate")),
        "pass_expl": _num(pp.get("explosiveness")),
        "rush_rate": _num(rp.get("rate")),
        "rush_ppa": _num(rp.get("ppa")),
        "rush_sr": _num(_f(rp, "successRate", "success_rate")),
        "rush_expl": _num(rp.get("explosiveness")),
        "havoc": _num(havoc.get("total")),
    }


def build_profiles(advanced, sp_ratings, season_stats):
    """Combine raw CFBD responses into {team: profile}. Pure function (easy to test)."""
    games_played = {}
    for row in season_stats:
        if _f(row, "statName", "stat_name") == "games":
            games_played[row["team"]] = _num(_f(row, "statValue", "stat_value"))

    sp = {r["team"]: r for r in sp_ratings if r.get("team")}
    profiles = {}
    for adv in advanced:
        team = adv.get("team")
        if not team:
            continue
        off, de = _side(adv, "offense"), _side(adv, "defense")
        if off["pass_rate"] is None and off["rush_rate"] is not None:
            off["pass_rate"] = 1 - off["rush_rate"]
        games = games_played.get(team)
        r = sp.get(team) or {}
        profiles[team] = {
            "team": team,
            "conference": adv.get("conference"),
            "games": games,
            "plays_per_game": round(off["plays"] / games, 1) if off["plays"] and games else None,
            "off": off,
            "def": de,
            "sp": {
                "rating": _num(r.get("rating")),
                "ranking": r.get("ranking"),
                "off": _num((r.get("offense") or {}).get("rating")),
                "def": _num((r.get("defense") or {}).get("rating")),
            },
        }

    # Percentiles across every team with data (FBS when synced with classification=fbs).
    def col(fn):
        return [fn(p) for p in profiles.values()]

    metrics = {
        "pass_rate": (lambda p: p["off"]["pass_rate"], True),
        "pass_ppa": (lambda p: p["off"]["pass_ppa"], True),
        "rush_ppa": (lambda p: p["off"]["rush_ppa"], True),
        "pass_expl": (lambda p: p["off"]["pass_expl"], True),
        "off_ppa": (lambda p: p["off"]["ppa"], True),
        "plays_per_game": (lambda p: p["plays_per_game"], True),
        # Defense: lower PPA allowed is better, so invert -> higher pct = stingier.
        "def_ppa": (lambda p: p["def"]["ppa"], False),
        "def_pass_ppa": (lambda p: p["def"]["pass_ppa"], False),
        "def_rush_ppa": (lambda p: p["def"]["rush_ppa"], False),
        "def_havoc": (lambda p: p["def"]["havoc"], True),
    }
    columns = {k: col(fn) for k, (fn, _) in metrics.items()}
    for p in profiles.values():
        p["pct"] = {k: _pct_rank(columns[k], fn(p), hib) for k, (fn, hib) in metrics.items()}
        p["style"] = style_label(p)
    return profiles


def style_label(p):
    pr = (p.get("pct") or {}).get("pass_rate")
    if pr is None:
        return "unknown"
    if pr >= 70:
        return "pass-heavy"
    if pr <= 30:
        return "run-heavy"
    return "balanced"


def fetch_profiles(season):
    advanced = _get("/stats/season/advanced", year=season, excludeGarbageTime="true")
    try:
        sp_ratings = _get("/ratings/sp", year=season)
    except Exception:
        sp_ratings = []
    try:
        season_stats = _get("/stats/season", year=season)
    except Exception:
        season_stats = []
    return build_profiles(advanced, sp_ratings, season_stats)


def _fmt(v, kind):
    if v is None:
        return "n/a"
    if kind == "pct":
        return f"{v * 100:.0f}%"
    if kind == "ppa":
        return f"{v:+.2f}"
    return f"{v:g}"


def _ord(n):
    if n is None:
        return "n/a"
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix} pct"


def describe(p):
    """Compact text block for the prompt."""
    if not p:
        return "  (no analytics available)"
    o, d, pct, sp = p["off"], p["def"], p["pct"], p["sp"]
    pace = (f", {p['plays_per_game']:g} plays/game ({_ord(pct['plays_per_game'])} pace)"
            if p["plays_per_game"] is not None else "")
    lines = [
        f"  Style: {p['style']} - passes on {_fmt(o['pass_rate'], 'pct')} of plays ({_ord(pct['pass_rate'])}){pace}",
        f"  Offense EPA/play {_fmt(o['ppa'], 'ppa')} ({_ord(pct['off_ppa'])}): "
        f"pass {_fmt(o['pass_ppa'], 'ppa')} ({_ord(pct['pass_ppa'])}), rush {_fmt(o['rush_ppa'], 'ppa')} ({_ord(pct['rush_ppa'])}); "
        f"success rate pass {_fmt(o['pass_sr'], 'pct')} / rush {_fmt(o['rush_sr'], 'pct')}; "
        f"pass explosiveness {_ord(pct['pass_expl'])}",
        f"  Defense EPA/play allowed {_fmt(d['ppa'], 'ppa')} ({_ord(pct['def_ppa'])}): "
        f"vs pass {_fmt(d['pass_ppa'], 'ppa')} ({_ord(pct['def_pass_ppa'])}), vs rush {_fmt(d['rush_ppa'], 'ppa')} ({_ord(pct['def_rush_ppa'])}); "
        f"havoc {_ord(pct['def_havoc'])}",
    ]
    if sp["rating"] is not None:
        lines.append(f"  SP+ {sp['rating']:+.1f} (#{sp['ranking']}), offense {_fmt(sp['off'], 'n')}, defense {_fmt(sp['def'], 'n')}")
    return "\n".join(lines)


def asof_profiles(hconn, season, as_of):
    """Season-to-date profiles using only games played before `as_of` (no leakage in replays)."""
    cutoff = as_of.astimezone(__import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    rows = hconn.execute("""
        SELECT t.team, COUNT(*) AS games, SUM(t.pass_att) AS pa, SUM(t.rush_att) AS ra,
               AVG(t.off_ppa) AS ppa, AVG(t.off_sr) AS sr, AVG(t.off_expl) AS expl,
               AVG(t.pass_ppa) AS pass_ppa, AVG(t.pass_sr) AS pass_sr, AVG(t.pass_expl) AS pass_expl,
               AVG(t.rush_ppa) AS rush_ppa, AVG(t.rush_sr) AS rush_sr, AVG(t.rush_expl) AS rush_expl,
               AVG(t.def_ppa) AS def_ppa, AVG(t.def_pass_ppa) AS def_pass_ppa, AVG(t.def_rush_ppa) AS def_rush_ppa,
               SUM(t.pass_att) + SUM(t.rush_att) AS plays
        FROM bt_team_games t JOIN bt_games g ON g.id = t.game_id
        WHERE g.season=? AND g.start_date < ? GROUP BY t.team HAVING COUNT(*) >= 1""", (season, cutoff)).fetchall()
    advanced, stats = [], []
    for r in rows:
        pr = r["pa"] / (r["pa"] + r["ra"]) if r["pa"] is not None and r["ra"] is not None and (r["pa"] + r["ra"]) else None
        advanced.append({"team": r["team"], "offense": {
            "plays": r["plays"], "ppa": r["ppa"], "successRate": r["sr"], "explosiveness": r["expl"],
            "passingPlays": {"rate": pr, "ppa": r["pass_ppa"], "successRate": r["pass_sr"], "explosiveness": r["pass_expl"]},
            "rushingPlays": {"rate": None if pr is None else 1 - pr, "ppa": r["rush_ppa"], "successRate": r["rush_sr"],
                             "explosiveness": r["rush_expl"]}},
            "defense": {"ppa": r["def_ppa"], "passingPlays": {"ppa": r["def_pass_ppa"]}, "rushingPlays": {"ppa": r["def_rush_ppa"]}}})
        stats.append({"team": r["team"], "statName": "games", "statValue": r["games"]})
    return build_profiles(advanced, [], stats)
