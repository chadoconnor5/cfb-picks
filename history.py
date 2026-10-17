"""Historical spreads, totals and stat lines from the backtest warehouse (backtest/backtest.db).

Lines are CFBD's consensus closing numbers (median across books), home perspective.
"""
import os
import sqlite3
import statistics

from grading import grade_ats, grade_ou

DB_PATH = os.getenv("BACKTEST_DB_PATH", os.path.join(os.path.dirname(__file__), "backtest", "backtest.db"))
STAT_KEYS = ("points", "rush_yds", "pass_yds", "total_yds", "sacks", "turnovers")


def connect():
    if not os.path.exists(DB_PATH):
        return None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def team_games(conn, team, seasons=None, as_of=None):
    """A team's games with lines and results, newest first, from the team's point of view."""
    q = "SELECT * FROM bt_games WHERE (home_team=? OR away_team=?) AND home_points IS NOT NULL"
    args = [team, team]
    if as_of:
        q += " AND start_date < ?"
        args.append(_iso(as_of))
    if seasons:
        q += f" AND season IN ({','.join('?' * len(seasons))})"
        args += list(seasons)
    out = []
    for g in conn.execute(q + " ORDER BY start_date DESC", args):
        g = dict(g)
        side = "home" if g["home_team"] == team else "away"
        spread = g["spread_close"]
        team_spread = None if spread is None else (spread if side == "home" else -spread)
        tp, op = (g["home_points"], g["away_points"]) if side == "home" else (g["away_points"], g["home_points"])
        out.append({
            "game_id": g["id"], "season": g["season"], "week": g["week"], "season_type": g["season_type"],
            "date": (g["start_date"] or "")[:10], "opponent": g["away_team"] if side == "home" else g["home_team"],
            "site": "neutral" if g["neutral_site"] else side, "conference_game": g.get("conference_game"),
            "team_spread": team_spread, "total": g["total_close"], "points": tp, "opp_points": op,
            "su": "W" if tp > op else "L" if tp < op else "T",
            "ats": grade_ats(side, spread, g["home_points"], g["away_points"]),
            "ou": grade_ou("over", g["total_close"], g["home_points"], g["away_points"]),
            "cover_margin": None if team_spread is None else tp + team_spread - op,
        })
    return out


def _iso(d):
    return d.astimezone(__import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _record(rows, key):
    w = sum(1 for r in rows if r[key] == "W")
    l = sum(1 for r in rows if r[key] == "L")
    p = sum(1 for r in rows if r[key] in ("P", "T"))
    return {"w": w, "l": l, "p": p, "pct": round(100 * w / (w + l), 1) if w + l else None}


def summarize_lines(rows):
    lined = [r for r in rows if r["team_spread"] is not None]
    def block(sub):
        margins = [r["cover_margin"] for r in sub if r["cover_margin"] is not None]
        totals = [r["points"] + r["opp_points"] - r["total"] for r in sub if r["total"] is not None]
        return {"n": len(sub), "su": _record(sub, "su"), "ats": _record(sub, "ats"), "ou": _record(sub, "ou"),
                "avg_cover_margin": round(statistics.mean(margins), 1) if margins else None,
                "avg_vs_total": round(statistics.mean(totals), 1) if totals else None}
    return {
        "all": block(lined),
        "home": block([r for r in lined if r["site"] == "home"]),
        "away": block([r for r in lined if r["site"] == "away"]),
        "neutral": block([r for r in lined if r["site"] == "neutral"]),
        "favorite": block([r for r in lined if r["team_spread"] < 0]),
        "underdog": block([r for r in lined if r["team_spread"] > 0]),
        "conference": block([r for r in lined if r["conference_game"]]),
        "last10": block(lined[:10]),
    }


def head_to_head(conn, a, b, limit=10, as_of=None):
    rows = [r for r in team_games(conn, a, as_of=as_of) if r["opponent"] == b][:limit]
    return rows


def stat_lines(conn, team, season, as_of=None):
    """Per-game stats for and against this season, for prop questions."""
    cutoff = _iso(as_of) if as_of else "9999"
    rows = conn.execute(
        """SELECT t.*, o.rush_yds AS opp_rush_yds, o.pass_yds AS opp_pass_yds, o.total_yds AS opp_total_yds,
                  o.sacks AS opp_sacks, o.turnovers AS opp_turnovers, o.points AS opp_points_box, g.start_date
           FROM bt_team_games t JOIN bt_games g ON g.id = t.game_id
           LEFT JOIN bt_team_games o ON o.game_id = t.game_id AND o.team = t.opponent
           WHERE t.team=? AND g.season=? AND g.start_date < ? ORDER BY g.start_date""", (team, season, cutoff)).fetchall()
    def agg(key):
        vals = [r[key] for r in rows if r[key] is not None]
        if not vals:
            return None
        return {"avg": round(statistics.mean(vals), 1), "last3": round(statistics.mean(vals[-3:]), 1),
                "min": min(vals), "max": max(vals), "n": len(vals),
                "sd": round(statistics.pstdev(vals), 1) if len(vals) > 1 else None}
    return {
        "for": {k: agg(k) for k in ("points", "rush_yds", "pass_yds", "total_yds", "sacks", "turnovers")},
        "against": {"points": agg("opp_points_box"), "rush_yds": agg("opp_rush_yds"), "pass_yds": agg("opp_pass_yds"),
                    "total_yds": agg("opp_total_yds"), "sacks": agg("opp_sacks"), "turnovers": agg("opp_turnovers")},
        "games": [{"opponent": r["opponent"], "date": (r["start_date"] or "")[:10],
                   **{k: r[k] for k in ("points", "rush_yds", "pass_yds", "total_yds", "sacks")},
                   "opp_sacks": r["opp_sacks"]} for r in rows],
    }


def _fmt_rec(r):
    s = f"{r['w']}-{r['l']}" + (f"-{r['p']}" if r["p"] else "")
    return s + (f" ({r['pct']}%)" if r["pct"] is not None else "")


def matchup(conn, home, away, season, home_spread=None, as_of=None):
    """Everything historical for one game, as a dict for UI + text for the prompt."""
    if conn is None:
        return None
    out = {"teams": {}, "h2h": head_to_head(conn, home, away, as_of=as_of)}
    for side, team in (("home", home), ("away", away)):
        this = team_games(conn, team, [season], as_of)
        last = team_games(conn, team, [season - 1], as_of)
        multi = team_games(conn, team, list(range(season - 4, season + 1)), as_of)
        out["teams"][side] = {"team": team, "season": summarize_lines(this), "last_season": summarize_lines(last),
                              "five_years": summarize_lines(multi), "stats": stat_lines(conn, team, season, as_of)}
    return out


def describe(m, home, away):
    if not m:
        return "  (no historical data; run python -m backtest.collect)"
    L = []
    for side, team in (("away", away), ("home", home)):
        t = m["teams"][side]
        s, f5 = t["season"], t["five_years"]
        role = "home" if side == "home" else "away"
        L.append(f"  {team}: this season ATS {_fmt_rec(s['all']['ats'])}, O/U {_fmt_rec(s['all']['ou'])} (overs first), "
                 f"avg cover margin {s['all']['avg_cover_margin']}, avg vs total {s['all']['avg_vs_total']}; "
                 f"last 10 ATS {_fmt_rec(s['last10']['ats'])}")
        L.append(f"    Last 5 seasons: ATS {_fmt_rec(f5['all']['ats'])}; {role} {_fmt_rec(f5[role]['ats'])}; "
                 f"as favorite {_fmt_rec(f5['favorite']['ats'])}; as underdog {_fmt_rec(f5['underdog']['ats'])}; "
                 f"O/U {_fmt_rec(f5['all']['ou'])}")
        st = t["stats"]
        def a(d, k):
            v = d.get(k)
            return f"{v['avg']} (last 3: {v['last3']})" if v else "n/a"
        L.append(f"    Per game this season - rush yds {a(st['for'], 'rush_yds')}, total yds {a(st['for'], 'total_yds')}, "
                 f"sacks {a(st['for'], 'sacks')}; allowed rush yds {a(st['against'], 'rush_yds')}, "
                 f"total yds {a(st['against'], 'total_yds')}, sacks allowed {a(st['against'], 'sacks')}")
    if m["h2h"]:
        L.append(f"  Recent meetings ({home}'s view):")
        for r in m["h2h"][:5]:
            ln = f"{r['team_spread']:+g}" if r["team_spread"] is not None else "no line"
            L.append(f"    {r['date']}: {r['points']}-{r['opp_points']} ({r['su']}), line {ln} -> ATS {r['ats'] or '-'}, "
                     f"total {r['total']} -> {r['ou'] or '-'}")
    return "\n".join(L)
