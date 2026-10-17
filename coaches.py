"""Coach profiles from the history warehouse.

- Head coach career splits (straight up + against the closing spread): vs ranked, as ranked, bowls,
  conference, rivalry, favorite/underdog, home/road, after a bye.
- Talent over/under: each team-season's SP+ vs what its talent composite predicts (that season's
  cross-team regression). A coach's average residual = "does more (or less) with what he has".
- Staff grading: for coordinators and position coaches (career stops come from the program-intel
  search), the units they ran are graded by FBS percentile in each season, plus the first-year change
  when they arrived somewhere new.

Seasons where a team had more than one head coach (interim changes) are skipped for game splits,
because games can't be attributed reliably.
"""
import statistics
from collections import defaultdict
from datetime import datetime, timezone

from analytics import _pct_rank
from grading import grade_ats
from situational import rivalry_name

ROLE_UNITS = {
    # role keyword -> list of (label, metric, higher_is_better)
    "offensive coordinator": [("offense EPA/play", "off_ppa", True)],
    "quarterback": [("passing EPA/play", "pass_ppa", True)],
    "wide receiver": [("passing EPA/play", "pass_ppa", True), ("pass explosiveness", "pass_expl", True)],
    "tight end": [("passing EPA/play", "pass_ppa", True)],
    "running back": [("rushing EPA/play", "rush_ppa", True)],
    "offensive line": [("rushing EPA/play", "rush_ppa", True), ("sacks allowed/game", "sacks_allowed", False)],
    "defensive coordinator": [("defense EPA/play allowed", "def_ppa", False)],
    "defensive line": [("rush defense EPA allowed", "def_rush_ppa", False), ("sacks/game", "sacks", True)],
    "linebacker": [("rush defense EPA allowed", "def_rush_ppa", False)],
    "defensive back": [("pass defense EPA allowed", "def_pass_ppa", False)],
    "secondary": [("pass defense EPA allowed", "def_pass_ppa", False)],
    "cornerback": [("pass defense EPA allowed", "def_pass_ppa", False)],
    "safet": [("pass defense EPA allowed", "def_pass_ppa", False)],
    "special teams": [("SP+ special teams", "st", True)],
    "head coach": [("SP+ overall", "sp", True)],
}


def _parse(s):
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _rec(results):
    w = sum(1 for r in results if r == "W")
    l = sum(1 for r in results if r == "L")
    p = sum(1 for r in results if r in ("P", "T"))
    return {"w": w, "l": l, "p": p, "pct": round(100 * w / (w + l), 1) if w + l else None}


# ---------- head coach ----------

def head_coach(conn, team, season):
    rows = conn.execute("SELECT * FROM bt_coaches WHERE team=? AND season<=? ORDER BY season DESC", (team, season)).fetchall()
    if not rows:
        return None
    latest = rows[0]["season"]
    current = [r for r in rows if r["season"] == latest]
    return current[-1]["coach"] if current else None


def career(conn, coach):
    return [dict(r) for r in conn.execute("SELECT * FROM bt_coaches WHERE coach=? ORDER BY season", (coach,))]


def _sole_seasons(conn, coach):
    out = []
    for c in career(conn, coach):
        n = conn.execute("SELECT COUNT(*) AS n FROM bt_coaches WHERE team=? AND season=?", (c["team"], c["season"])).fetchone()["n"]
        if n == 1:
            out.append((c["season"], c["team"]))
    return out


def _ap_rank(conn, season, team, week, season_type):
    if season_type == "regular":
        r = conn.execute("""SELECT rank FROM bt_rankings WHERE season=? AND team=? AND poll LIKE 'AP%' AND season_type='regular'
                            AND week<=? ORDER BY week DESC LIMIT 1""", (season, team, week)).fetchone()
    else:
        r = conn.execute("""SELECT rank FROM bt_rankings WHERE season=? AND team=? AND poll LIKE 'AP%' AND season_type='regular'
                            ORDER BY week DESC LIMIT 1""", (season, team)).fetchone()
    return r["rank"] if r else None


def coach_games(conn, coach, as_of=None):
    as_of = as_of or datetime.now(timezone.utc)
    games = []
    for season, team in _sole_seasons(conn, coach):
        rows = conn.execute("""SELECT * FROM bt_games WHERE season=? AND (home_team=? OR away_team=?)
                               AND home_points IS NOT NULL ORDER BY start_date""", (season, team, team)).fetchall()
        prev = None
        for g in rows:
            if not g["start_date"] or _parse(g["start_date"]) >= as_of:
                continue
            side = "home" if g["home_team"] == team else "away"
            opp = g["away_team"] if side == "home" else g["home_team"]
            tp, op = (g["home_points"], g["away_points"]) if side == "home" else (g["away_points"], g["home_points"])
            spread = g["spread_close"]
            rest = (_parse(g["start_date"]) - _parse(prev["start_date"])).days if prev else None
            games.append({
                "season": season, "team": team, "opponent": opp, "site": "neutral" if g["neutral_site"] else side,
                "su": "W" if tp > op else "L" if tp < op else "T",
                "ats": grade_ats(side, spread, g["home_points"], g["away_points"]),
                "team_spread": None if spread is None else (spread if side == "home" else -spread),
                "opp_rank": _ap_rank(conn, season, opp, g["week"], g["season_type"]),
                "own_rank": _ap_rank(conn, season, team, g["week"], g["season_type"]),
                "bowl": g["season_type"] == "postseason", "conference": bool(g["conference_game"]),
                "rivalry": bool(rivalry_name(team, opp)), "after_bye": rest is not None and rest >= 12,
            })
            prev = g
    return games


def coach_splits(games):
    def split(sub):
        return {"n": len(sub), "su": _rec([g["su"] for g in sub]), "ats": _rec([g["ats"] for g in sub])}
    return {
        "overall": split(games),
        "vs_ranked": split([g for g in games if g["opp_rank"]]),
        "vs_top10": split([g for g in games if g["opp_rank"] and g["opp_rank"] <= 10]),
        "as_ranked": split([g for g in games if g["own_rank"]]),
        "bowls": split([g for g in games if g["bowl"]]),
        "conference": split([g for g in games if g["conference"]]),
        "rivalry": split([g for g in games if g["rivalry"]]),
        "favorite": split([g for g in games if g["team_spread"] is not None and g["team_spread"] < 0]),
        "underdog": split([g for g in games if g["team_spread"] is not None and g["team_spread"] > 0]),
        "home": split([g for g in games if g["site"] == "home"]),
        "road": split([g for g in games if g["site"] == "away"]),
        "after_bye": split([g for g in games if g["after_bye"]]),
    }


# ---------- talent over/under ----------

def talent_residuals(conn):
    """{(season, team): SP+ minus SP+ predicted from talent that season}."""
    out = {}
    seasons = [r["season"] for r in conn.execute("SELECT DISTINCT season FROM bt_talent")]
    for season in seasons:
        rows = conn.execute("""SELECT t.team, t.talent, s.rating FROM bt_talent t JOIN bt_sp s
                               ON s.season=t.season AND s.team=t.team WHERE t.season=? AND t.talent IS NOT NULL
                               AND s.rating IS NOT NULL""", (season,)).fetchall()
        if len(rows) < 10:
            continue
        x = [r["talent"] for r in rows]
        y = [r["rating"] for r in rows]
        mx, my = statistics.mean(x), statistics.mean(y)
        vx = sum((a - mx) ** 2 for a in x)
        b = sum((a - mx) * (c - my) for a, c in zip(x, y)) / vx if vx else 0
        for r in rows:
            out[(season, r["team"])] = round(r["rating"] - (my + b * (r["talent"] - mx)), 1)
    return out


def coach_talent_score(conn, coach, max_season):
    res = talent_residuals(conn)
    vals = [(s, t, res[(s, t)]) for s, t in _sole_seasons(conn, coach) if (s, t) in res and s <= max_season]
    if not vals:
        return None
    return {"avg_residual": round(statistics.mean(v[2] for v in vals), 1), "seasons": len(vals),
            "by_season": [{"season": s, "team": t, "residual": r} for s, t, r in vals]}


def team_talent(conn, team, season):
    """Talent composite rank for the season (falls back to the latest season available)."""
    row = conn.execute("SELECT MAX(season) AS s FROM bt_talent WHERE season<=? AND team=?", (season, team)).fetchone()
    if not row or row["s"] is None:
        return None
    rows = conn.execute("SELECT team, talent FROM bt_talent WHERE season=? AND talent IS NOT NULL", (row["s"],)).fetchall()
    me = next((r["talent"] for r in rows if r["team"] == team), None)
    if me is None:
        return None
    rank = 1 + sum(1 for r in rows if r["talent"] > me)
    return {"talent": me, "rank": rank, "of": len(rows), "season": row["s"]}


# ---------- staff grading ----------

def unit_percentiles(conn, season):
    """{team: {metric: pct}} for one season from per-game data + SP+."""
    rows = conn.execute("""
        SELECT t.team, AVG(t.off_ppa) AS off_ppa, AVG(t.pass_ppa) AS pass_ppa, AVG(t.rush_ppa) AS rush_ppa,
               AVG(t.pass_expl) AS pass_expl, AVG(t.def_ppa) AS def_ppa, AVG(t.def_pass_ppa) AS def_pass_ppa,
               AVG(t.def_rush_ppa) AS def_rush_ppa, AVG(t.sacks) AS sacks, AVG(o.sacks) AS sacks_allowed
        FROM bt_team_games t JOIN bt_games g ON g.id=t.game_id
        LEFT JOIN bt_team_games o ON o.game_id=t.game_id AND o.team=t.opponent
        WHERE g.season=? GROUP BY t.team HAVING COUNT(*) >= 4""", (season,)).fetchall()
    sp = {r["team"]: dict(r) for r in conn.execute("SELECT team, rating AS sp, st FROM bt_sp WHERE season=?", (season,))}
    data = {r["team"]: {**dict(r), **{k: v for k, v in sp.get(r["team"], {}).items() if k != "team"}} for r in rows}
    metrics = ["off_ppa", "pass_ppa", "rush_ppa", "pass_expl", "def_ppa", "def_pass_ppa", "def_rush_ppa", "sacks",
               "sacks_allowed", "sp", "st"]
    lower_better = {"def_ppa", "def_pass_ppa", "def_rush_ppa", "sacks_allowed"}
    cols = {m: [d.get(m) for d in data.values()] for m in metrics}
    return {t: {m: _pct_rank(cols[m], d.get(m), higher_is_better=m not in lower_better) for m in metrics}
            for t, d in data.items()}


def role_units(role):
    r = (role or "").lower()
    if "coordinator" in r and "offens" in r:
        return ROLE_UNITS["offensive coordinator"]
    if "coordinator" in r and "defens" in r:
        return ROLE_UNITS["defensive coordinator"]
    for key, units in ROLE_UNITS.items():
        if key in r:
            return units
    return []


def grade_staff_member(conn, member, max_season, cache=None):
    """member: {name, role, career: [{team, role, start, end}]} -> graded stops."""
    cache = cache if cache is not None else {}
    stops = []
    for stop in member.get("career") or []:
        units = role_units(stop.get("role") or member.get("role"))
        if not units or not stop.get("team") or stop.get("start") is None:
            continue
        end = min(stop.get("end") or max_season, max_season)
        seasons = []
        for season in range(int(stop["start"]), int(end) + 1):
            if season not in cache:
                cache[season] = unit_percentiles(conn, season)
            pct = cache[season].get(stop["team"])
            if pct:
                seasons.append({"season": season, **{label: pct.get(metric) for label, metric, _ in units}})
        if not seasons:
            continue
        first, prior_year = int(stop["start"]), int(stop["start"]) - 1
        if prior_year not in cache:
            cache[prior_year] = unit_percentiles(conn, prior_year)
        before = cache[prior_year].get(stop["team"])
        label, metric, _ = units[0]
        y1 = next((s.get(label) for s in seasons if s["season"] == first), None)
        stops.append({"team": stop["team"], "role": stop.get("role") or member.get("role"),
                      "years": f"{stop['start']}-{stop.get('end') or 'now'}", "seasons": seasons,
                      "avg": {lbl: round(statistics.mean(s[lbl] for s in seasons if s.get(lbl) is not None), 0)
                              if any(s.get(lbl) is not None for s in seasons) else None for lbl, _, _ in units},
                      "first_year_change": (y1 - before[metric]) if (y1 is not None and before and before.get(metric) is not None) else None,
                      "primary_metric": label})
    return stops


def describe_staff(graded):
    lines = []
    for m in graded:
        stops = m.get("graded") or []
        if not stops:
            lines.append(f"  {m['role']}: {m['name']}" + (" (play-caller)" if m.get("play_caller") else "") +
                         (f" - since {m['since']}" if m.get("since") else "") + " (no FBS unit history in data)")
            continue
        parts = []
        for s in stops[-3:]:
            pm = s["primary_metric"]
            fy = f", year-1 change {s['first_year_change']:+d} pct pts" if s.get("first_year_change") is not None else ""
            parts.append(f"{s['team']} {s['years']} ({s['role']}): {pm} avg {s['avg'].get(pm)}th pct{fy}")
        lines.append(f"  {m['role']}: {m['name']}" + (" (play-caller)" if m.get("play_caller") else "") + " - " + "; ".join(parts))
    return "\n".join(lines)


def _fmt(r):
    s = r["su"]
    a = r["ats"]
    return f"{s['w']}-{s['l']} SU, {a['w']}-{a['l']}{'-' + str(a['p']) if a['p'] else ''} ATS" if r["n"] else "none"


def profile(conn, team, season, as_of=None):
    """Head coach profile for a team (splits + talent score + team talent)."""
    if conn is None:
        return None
    coach = head_coach(conn, team, season)
    out = {"team": team, "coach": coach, "talent": team_talent(conn, team, season)}
    if coach:
        car = career(conn, coach)
        out["tenure"] = {"seasons_here": sum(1 for c in car if c["team"] == team),
                         "first_season_here": min((c["season"] for c in car if c["team"] == team), default=None),
                         "stops": sorted({c["team"] for c in car})}
        out["splits"] = coach_splits(coach_games(conn, coach, as_of))
        out["talent_score"] = coach_talent_score(conn, coach, season - 1)   # prior seasons only (no in-season leak)
    return out


def describe(p):
    if not p:
        return "  (no coaching data)"
    if not p.get("coach"):
        return f"  {p['team']}: head coach unknown in data"
    s = p.get("splits") or {}
    t = p.get("tenure") or {}
    lines = [f"  {p['team']}: HC {p['coach']} (season {t.get('seasons_here')} here, since {t.get('first_season_here')})"]
    if s:
        lines.append(f"    Career in data: {_fmt(s['overall'])}; vs ranked {_fmt(s['vs_ranked'])}; vs top 10 {_fmt(s['vs_top10'])}; "
                     f"as ranked {_fmt(s['as_ranked'])}")
        lines.append(f"    Bowls {_fmt(s['bowls'])}; conference {_fmt(s['conference'])}; rivalry {_fmt(s['rivalry'])}; "
                     f"favorite {_fmt(s['favorite'])}; underdog {_fmt(s['underdog'])}; road {_fmt(s['road'])}; after bye {_fmt(s['after_bye'])}")
    ts = p.get("talent_score")
    if ts:
        lines.append(f"    Results vs roster talent: {ts['avg_residual']:+.1f} SP+ pts per season vs what talent predicts "
                     f"({ts['seasons']} seasons; + means does more with less)")
    if p.get("talent"):
        lines.append(f"    Roster talent ({p['talent']['season']}): No. {p['talent']['rank']} of {p['talent']['of']} (247 composite)")
    return "\n".join(lines)
