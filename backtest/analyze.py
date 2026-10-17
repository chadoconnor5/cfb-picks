"""Measure how weather changed team performance, by offensive style, and test our rules.

    python -m backtest.analyze

Reads backtest/backtest.db (from backtest.collect) and writes:
  backtest/report.md          human-readable findings
  backtest/calibration.json   evidence the app shows Claude for similar conditions

Method
- Weather: the actual game-window conditions (Open-Meteo archive), graded by the same
  conditions.classify() the app uses on forecasts. Indoor games are excluded.
- Effect on a team: that game's stat minus the team's average in its OTHER games that season
  (leave-one-out baseline), so a team is only compared with itself.
- Style: built only from games BEFORE the matchup (prior season early on), so rule tests have no
  look-ahead. Percentiles are taken among all teams at the same point of the season.
- Market: points vs the closing consensus total, and covers vs the closing spread. A real edge
  means beating the line, not just scoring less.
"""
import argparse
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analytics  # noqa: E402
import conditions  # noqa: E402
from backtest.collect import connect  # noqa: E402
from grading import grade_ats, grade_ou  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
METRICS = ["pass_rate", "off_ppa", "pass_ppa", "rush_ppa", "off_sr", "pass_sr", "rush_sr", "turnovers"]
PROFILE_METRICS = ["pass_rate", "pass_ppa", "rush_ppa", "pass_expl", "off_ppa", "def_rush_ppa"]
MIN_PRIOR_GAMES = 3
MIN_LOO_GAMES = 4
SMALL_SAMPLE = 30


# ---------- loading ----------

def load(conn):
    games = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM bt_games WHERE home_points IS NOT NULL")}
    wx = {r["game_id"]: json.loads(r["data"]) for r in conn.execute("SELECT * FROM bt_weather")}
    tgs = []
    for r in conn.execute("SELECT * FROM bt_team_games"):
        g = games.get(r["game_id"])
        if not g:
            continue
        d = dict(r)
        att = (d["pass_att"] or 0) + (d["rush_att"] or 0)
        d["pass_rate"] = d["pass_att"] / att if d["pass_att"] is not None and d["rush_att"] is not None and att else None
        d.update(season=g["season"], season_type=g["season_type"], week=g["week"], start_date=g["start_date"] or "")
        d["order"] = (g["season"], 0 if g["season_type"] == "regular" else 1, g["week"] or 0, d["start_date"])
        tgs.append(d)
    return games, wx, tgs


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


# ---------- features ----------

def add_loo_deltas(tgs):
    """d_<metric> = this game minus the team's mean over its other games that season."""
    sums = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))
    for t in tgs:
        for m in METRICS:
            if t[m] is not None:
                s = sums[(t["season"], t["team"])][m]
                s[0] += t[m]
                s[1] += 1
    for t in tgs:
        for m in METRICS:
            total, n = sums[(t["season"], t["team"])][m]
            if t[m] is None or n - 1 < MIN_LOO_GAMES:
                t[f"d_{m}"] = None
            else:
                t[f"d_{m}"] = t[m] - (total - t[m]) / (n - 1)


def add_pregame_profiles(tgs):
    """Attach t['profile'] built from prior games only, with same-week percentiles."""
    by_team = defaultdict(list)
    for t in tgs:
        by_team[t["team"]].append(t)
    season_means = {}
    for team, rows in by_team.items():
        rows.sort(key=lambda r: r["order"])
        by_season = defaultdict(list)
        for r in rows:
            by_season[r["season"]].append(r)
        for season, srows in by_season.items():
            season_means[(team, season)] = ({m: _mean(r[m] for r in srows) for m in PROFILE_METRICS}, len(srows))

    for team, rows in by_team.items():
        for i, r in enumerate(rows):
            prior = [p for p in rows[:i] if p["season"] == r["season"]]
            if len(prior) >= MIN_PRIOR_GAMES:
                r["raw_profile"] = {m: _mean(p[m] for p in prior) for m in PROFILE_METRICS}
            else:
                prev = season_means.get((team, r["season"] - 1))
                r["raw_profile"] = prev[0] if prev and prev[1] >= 6 else None

    slots = defaultdict(list)
    for t in tgs:
        if t.get("raw_profile"):
            slots[(t["season"], t["season_type"], t["week"])].append(t)
    for rows in slots.values():
        cols = {m: [r["raw_profile"][m] for r in rows] for m in PROFILE_METRICS}
        for r in rows:
            rp = r["raw_profile"]
            pct = {m: analytics._pct_rank(cols[m], rp[m], higher_is_better=(m != "def_rush_ppa")) for m in PROFILE_METRICS}
            prof = {"team": r["team"], "off": {"pass_rate": rp["pass_rate"]}, "pct": pct}
            prof["style"] = analytics.style_label(prof)
            r["profile"] = prof
    for t in tgs:
        t.setdefault("profile", None)


def evaluate_games(games, wx, tgs):
    """One record per outdoor game with weather: conditions, rule outputs, market outcomes."""
    by_game = defaultdict(dict)
    for t in tgs:
        by_game[t["game_id"]][t["team"]] = t
    out = []
    for gid, w in wx.items():
        g = games.get(gid)
        teams = by_game.get(gid, {})
        if not g or g["home_team"] not in teams or g["away_team"] not in teams:
            continue
        home, away = teams[g["home_team"]], teams[g["away_team"]]
        cond = conditions.classify(w)
        a = conditions.assess(home["profile"], away["profile"], w, cond, g["home_team"], g["away_team"])
        hp, ap = g["home_points"], g["away_points"]
        rec = {
            "game": g, "weather": w, "cond": cond, "assessment": a, "home": home, "away": away,
            "total_pts": hp + ap,
            "vs_total": (hp + ap - g["total_close"]) if g["total_close"] is not None else None,
            "under": grade_ou("under", g["total_close"], hp, ap),
            "edge_ats": grade_ats(a["edge"], g["spread_close"], hp, ap) if a["edge"] else None,
        }
        for side, t in (("home", home), ("away", away)):
            t["cond"] = cond
            t["label"] = a["teams"][side]["label"]
            t["exposure_score"] = a["teams"][side]["exposure_score"]
            t["cover"] = grade_ats(side, g["spread_close"], hp, ap)
        out.append(rec)
    return out


# ---------- stats helpers ----------

def wilson(w, n, z=1.96):
    if n == 0:
        return None, None
    p = w / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(100 * (c - h), 1), round(100 * (c + h), 1)


def rate(results):
    w = sum(1 for r in results if r == "W")
    l = sum(1 for r in results if r == "L")
    n = w + l
    lo, hi = wilson(w, n)
    return {"w": w, "l": l, "n": n, "pct": round(100 * w / n, 1) if n else None, "ci95": [lo, hi]}


def team_effects(rows):
    out = {"n": len(rows)}
    for m in ("pass_rate", "off_ppa", "pass_ppa", "rush_ppa", "off_sr", "turnovers"):
        v = _mean(r.get(f"d_{m}") for r in rows)
        out[f"d_{m}"] = round(v, 3) if v is not None else None
    return out


def game_bucket(recs):
    vs = [r["vs_total"] for r in recs if r["vs_total"] is not None]
    return {"n_games": len(recs), "pts_vs_total": round(sum(vs) / len(vs), 2) if vs else None,
            "avg_total_pts": round(_mean(r["total_pts"] for r in recs), 1) if recs else None,
            "under": rate([r["under"] for r in recs])}


def ols(X, y, names):
    import numpy as np
    X, y = np.asarray(X, float), np.asarray(y, float)
    # Drop terms that never vary in this sample (e.g. no snow games); they can't be estimated.
    keep = [0] + [j for j in range(1, X.shape[1]) if X[:, j].std() > 1e-9]
    X, names = X[:, keep], [names[j] for j in keep]
    n, k = X.shape
    if n <= k + 5:
        return None
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    sigma2 = resid @ resid / (n - k)
    cov = sigma2 * np.linalg.pinv(X.T @ X)
    se = np.sqrt(np.diag(cov))
    return {"n": int(n), "r2": round(float(1 - resid @ resid / ((y - y.mean()) @ (y - y.mean()))), 4),
            "coef": {nm: {"b": round(float(b), 4), "se": round(float(s), 4), "t": round(float(b / s), 2) if s else None}
                     for nm, b, s in zip(names, beta, se)}}


# ---------- analysis ----------

def analyze(games, wx, tgs, stories=None):
    add_loo_deltas(tgs)
    add_pregame_profiles(tgs)
    recs = evaluate_games(games, wx, tgs)
    team_rows = [t for r in recs for t in (r["home"], r["away"])]

    res = {"meta": {"generated_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
                    "seasons": sorted({r["game"]["season"] for r in recs}),
                    "n_games": len(recs), "n_team_games": len(team_rows)},
           "all_games": game_bucket(recs)}

    # Scoring vs the market by factor level.
    for factor in ("wind", "temp", "level"):
        groups = defaultdict(list)
        for r in recs:
            groups[r["cond"][factor]].append(r)
        res[factor] = {k: game_bucket(v) for k, v in groups.items()}
    precip = defaultdict(list)
    for r in recs:
        c = r["cond"]
        precip[f"{c['precip_type']}:{c['precip']}" if c["precip_type"] else "dry"].append(r)
    res["precip"] = {k: game_bucket(v) for k, v in precip.items()}

    # Style x conditions on efficiency (vs the team's own baseline).
    def style_split(key_fn):
        groups = defaultdict(lambda: defaultdict(list))
        for t in team_rows:
            if t["profile"]:
                groups[key_fn(t)][t["profile"]["style"]].append(t)
        return {k: {s: team_effects(v) for s, v in sv.items()} for k, sv in groups.items()}

    res["style_by_wind"] = style_split(lambda t: t["cond"]["wind"])
    res["style_by_precip"] = style_split(
        lambda t: f"{t['cond']['precip_type']}:{t['cond']['precip']}" if t["cond"]["precip_type"] else "dry")
    res["style_by_level"] = style_split(lambda t: t["cond"]["level"])

    for factor, key in (("wind", "style_by_wind"), ("precip", "style_by_precip"), ("level", "style_by_level")):
        for lvl, styles in res[key].items():
            if lvl in res[factor]:
                res[factor][lvl]["by_style"] = styles

    # Do the rules work? (pregame profiles only)
    labels = defaultdict(list)
    for t in team_rows:
        labels[t["label"]].append(t)
    res["rules"] = {
        "labels": {k: {**team_effects(v), "ats": rate([t["cover"] for t in v])} for k, v in labels.items()},
        "edge_ats": rate([r["edge_ats"] for r in recs if r["edge_ats"]]),
        "under_lean": {**game_bucket([r for r in recs if r["assessment"]["total_lean"] == "under"])},
        "no_lean": {**game_bucket([r for r in recs if r["assessment"]["total_lean"] is None])},
    }

    # Regressions: continuous weather, and the style interaction we care about.
    X, y, X2, y2 = [], [], [], []
    wx_by_game = {r["game"]["id"]: r["weather"] for r in recs}
    for t in team_rows:
        w = wx_by_game[t["game_id"]]
        if t.get("d_off_ppa") is None or not t["profile"] or t["profile"]["pct"]["pass_rate"] is None:
            continue
        pr = (t["profile"]["pct"]["pass_rate"] - 50) / 50
        wind, snow = w.get("wind_mph") or 0, w.get("snow_in") or 0
        rain = max(0.0, (w.get("precip_in") or 0) - snow / 10)
        cold = 1.0 if (w.get("temp_min_f") or 99) <= 32 else 0.0
        X.append([1, wind, rain, snow, cold, pr, wind * pr, rain * pr])
        y.append(t["d_off_ppa"])
        if t.get("d_pass_rate") is not None:
            X2.append([1, wind, rain, snow, cold, pr, wind * pr, rain * pr])
            y2.append(t["d_pass_rate"])
    names = ["const", "wind_mph", "rain_in", "snow_in", "cold", "pass_heavy", "wind_x_pass_heavy", "rain_x_pass_heavy"]
    Xg, yg = [], []
    for r in recs:
        if r["vs_total"] is None:
            continue
        w = r["weather"]
        snow = w.get("snow_in") or 0
        Xg.append([1, w.get("wind_mph") or 0, max(0.0, (w.get("precip_in") or 0) - snow / 10), snow,
                   1.0 if (w.get("temp_min_f") or 99) <= 32 else 0.0])
        yg.append(r["vs_total"])
    res["regression"] = {
        "team_off_epa_delta": ols(X, y, names) if X else None,
        "team_pass_rate_delta": ols(X2, y2, names) if X2 else None,
        "game_points_vs_total": ols(Xg, yg, names[:5]) if Xg else None,
    }

    res["stories"] = match_stories(stories or [], recs)
    res["extreme_games"] = [summarize(r) for r in sorted(recs, key=lambda r: -r["cond"]["passing_hostility"])[:25]]
    return res


def summarize(r):
    g, w, c = r["game"], r["weather"], r["cond"]
    def team(t):
        return {"team": t["team"], "style": (t["profile"] or {}).get("style"), "label": t["label"],
                "d_pass_rate": _round(t.get("d_pass_rate")), "d_pass_ppa": _round(t.get("d_pass_ppa")),
                "d_rush_ppa": _round(t.get("d_rush_ppa")), "d_off_ppa": _round(t.get("d_off_ppa"))}
    return {"game_id": g["id"], "season": g["season"], "week": g["week"], "date": (g["start_date"] or "")[:10],
            "matchup": f"{g['away_team']} at {g['home_team']}", "score": f"{g['away_points']}-{g['home_points']}",
            "total_close": g["total_close"], "vs_total": r["vs_total"], "level": c["level"],
            "weather": {k: w.get(k) for k in ("temp_f", "wind_mph", "gust_mph", "precip_in", "snow_in")},
            "away": team(r["away"]), "home": team(r["home"])}


def _round(v, nd=3):
    return round(v, nd) if v is not None else None


def match_stories(stories, recs):
    out = []
    for s in stories:
        teams = {s["home"].lower(), s["away"].lower()}
        hit = next((r for r in recs if r["game"]["season"] == s["season"]
                    and {r["game"]["home_team"].lower(), r["game"]["away_team"].lower()} == teams
                    and (not s.get("date") or (r["game"]["start_date"] or "").startswith(s["date"]))), None)
        out.append({"story": s, "matched": summarize(hit) if hit else None})
    return out


# ---------- outputs ----------

def calibration(res):
    """Compact evidence for the app, keyed the same way conditions.classify() labels things."""
    def slim(b):
        return {"n_games": b["n_games"], "pts_vs_total": b["pts_vs_total"], "under_pct": b["under"]["pct"],
                "under_n": b["under"]["n"], "by_style": b.get("by_style", {})}
    return {"meta": res["meta"], "baseline_under_pct": res["all_games"]["under"]["pct"],
            "wind": {k: slim(v) for k, v in res["wind"].items()},
            "precip": {k: slim(v) for k, v in res["precip"].items()},
            "level": {k: slim(v) for k, v in res["level"].items()},
            "rules": {"edge_ats": res["rules"]["edge_ats"], "under_lean": res["rules"]["under_lean"]["under"],
                      "labels": {k: {"n": v["n"], "d_off_ppa": v["d_off_ppa"], "ats_pct": v["ats"]["pct"]}
                                 for k, v in res["rules"]["labels"].items()}}}


def _fmt_rate(r):
    if not r or not r["n"]:
        return "n/a"
    flag = " ⚠ small" if r["n"] < SMALL_SAMPLE else ""
    return f"{r['pct']}% ({r['w']}-{r['l']}, 95% CI {r['ci95'][0]}–{r['ci95'][1]}){flag}"


def _fmt(v, nd=3, signed=True):
    if v is None:
        return "—"
    return f"{v:+.{nd}f}" if signed else f"{v:.{nd}f}"


ORDER = {"wind": ["none", "mild", "moderate", "severe"], "temp": ["none", "mild", "moderate", "severe"],
         "level": ["none", "mild", "significant", "extreme"]}


def report_md(res):
    m = res["meta"]
    L = [f"# Weather backtest", "",
         f"Seasons {m['seasons'][0] if m['seasons'] else '?'}–{m['seasons'][-1] if m['seasons'] else '?'} · "
         f"{m['n_games']} outdoor FBS games with weather · {m['n_team_games']} team-games · generated {m['generated_at']}", "",
         f"Baseline: unders {_fmt_rate(res['all_games']['under'])} vs the closing total.", "",
         "Effects are versus each team's own average in its other games that season (EPA per play; pass rate as a share of plays).", ""]

    def scoring_table(title, key, order=None):
        L.extend([f"## {title}", "", "| Level | Games | Pts vs closing total | Unders | Avg total pts |", "|---|---|---|---|---|"])
        keys = order or sorted(res[key])
        for k in keys:
            b = res[key].get(k)
            if b:
                L.append(f"| {k} | {b['n_games']} | {_fmt(b['pts_vs_total'], 1)} | {_fmt_rate(b['under'])} | {b['avg_total_pts']} |")
        L.append("")

    def style_table(title, key, order=None):
        L.extend([f"## {title}", "", "| Conditions | Style | Team-games | Δ pass rate | Δ off EPA | Δ pass EPA | Δ rush EPA | Δ turnovers |",
                  "|---|---|---|---|---|---|---|---|"])
        for k in (order or sorted(res[key])):
            for s in ("pass-heavy", "balanced", "run-heavy"):
                e = res[key].get(k, {}).get(s)
                if e:
                    flag = " ⚠" if e["n"] < SMALL_SAMPLE else ""
                    L.append(f"| {k} | {s} | {e['n']}{flag} | {_fmt(e['d_pass_rate'])} | {_fmt(e['d_off_ppa'])} | "
                             f"{_fmt(e['d_pass_ppa'])} | {_fmt(e['d_rush_ppa'])} | {_fmt(e['d_turnovers'], 2)} |")
        L.append("")

    scoring_table("Scoring vs the market by wind", "wind", ORDER["wind"])
    scoring_table("Scoring vs the market by precipitation", "precip")
    scoring_table("Scoring vs the market by temperature", "temp", ORDER["temp"])
    scoring_table("Scoring vs the market by overall passing-hostility level", "level", ORDER["level"])
    style_table("Style × wind: what changes for each offense", "style_by_wind", ORDER["wind"])
    style_table("Style × precipitation", "style_by_precip")
    style_table("Style × overall level", "style_by_level", ORDER["level"])

    r = res["rules"]
    L.extend(["## Do the app's rules work? (pregame profiles, closing lines)", "",
              "| Label | Team-games | Δ off EPA | ATS vs closing spread |", "|---|---|---|---|"])
    for k in ("exposed", "neutral", "insulated", "unaffected", "unknown"):
        v = r["labels"].get(k)
        if v:
            L.append(f"| {k} | {v['n']} | {_fmt(v['d_off_ppa'])} | {_fmt_rate(v['ats'])} |")
    L.extend(["", f"- Style-edge side ATS: {_fmt_rate(r['edge_ats'])}",
              f"- Games flagged \"lean under\": {r['under_lean']['n_games']} games, unders {_fmt_rate(r['under_lean']['under'])}, "
              f"{_fmt(r['under_lean']['pts_vs_total'], 1)} pts vs total",
              f"- Games with no lean: unders {_fmt_rate(r['no_lean']['under'])}", ""])

    L.extend(["## Regressions", ""])
    for name, reg in res["regression"].items():
        if not reg:
            continue
        L.extend([f"**{name}** (n={reg['n']}, R²={reg['r2']})", "", "| Term | Coef | SE | t |", "|---|---|---|---|"])
        for term, c in reg["coef"].items():
            L.append(f"| {term} | {c['b']:+.4f} | {c['se']:.4f} | {c['t']} |")
        L.append("")
    L.extend(["Reading it: `wind_x_pass_heavy` is the extra change per mph of wind for a fully pass-heavy offense "
              "(pass_heavy = +1 at the 100th percentile of pass rate, −1 at the 0th). |t| ≥ 2 is roughly significant.", ""])

    L.extend(["## Stories vs data", "", "| Game | Reported | Measured weather | Level | Score | vs total | Away Δ pass rate / pass EPA / rush EPA | Home Δ |",
              "|---|---|---|---|---|---|---|---|"])
    for s in res["stories"]:
        st, mt = s["story"], s["matched"]
        if not mt:
            L.append(f"| {st['away']} at {st['home']} ({st['season']}) | {st['reported']} | not found in data | | | | | |")
            continue
        w = mt["weather"]

        def deltas(t):
            return f"{_fmt(t['d_pass_rate'], 2)} / {_fmt(t['d_pass_ppa'], 2)} / {_fmt(t['d_rush_ppa'], 2)}"
        L.append(f"| {mt['matchup']} ({mt['date']}) | {st['reported']} | {w['temp_f']}F, wind {w['wind_mph']} (g {w['gust_mph']}), "
                 f"precip {w['precip_in']}\", snow {w['snow_in']}\" | {mt['level']} | {mt['score']} | {_fmt(mt['vs_total'], 1)} | "
                 f"{deltas(mt['away'])} | {deltas(mt['home'])} |")
    L.extend(["", "## Most extreme games in the sample", "", "| Game | Weather | Score | vs total | Away (style, label, Δ off EPA) | Home |", "|---|---|---|---|---|---|"])
    for e in res["extreme_games"]:
        w = e["weather"]
        L.append(f"| {e['matchup']} ({e['date']}) | {w['temp_f']}F, wind {w['wind_mph']} (g {w['gust_mph']}), precip {w['precip_in']}\", "
                 f"snow {w['snow_in']}\" | {e['score']} | {_fmt(e['vs_total'], 1)} | "
                 f"{e['away']['style']}, {e['away']['label']}, {_fmt(e['away']['d_off_ppa'], 2)} | "
                 f"{e['home']['style']}, {e['home']['label']}, {_fmt(e['home']['d_off_ppa'], 2)} |")
    L.extend(["", "## Caveats", "",
              "- Archive weather is reanalysis (about 10-30 km grid) and smooths stadium-level gusts and downpours; compare the Stories table with what was reported.",
              "- Live picks use forecasts, which are noisier than the archive, so live performance will be somewhat weaker than this backtest.",
              "- Closing lines already price forecast weather, so weather can move scoring a lot and still show little edge against the total.",
              "- Rows marked ⚠ have under 30 samples; treat them as anecdotes.", ""])
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None)
    ap.add_argument("--out", default=HERE)
    ap.add_argument("--include-current", action="store_true", help="include the in-progress season (leaks into replays)")
    args = ap.parse_args(argv)
    conn = connect(args.db)
    games, wx, tgs = load(conn)
    if not args.include_current:   # keep the in-progress season out so its weeks can be replayed blind
        year = datetime.now(timezone.utc).year
        games = {k: v for k, v in games.items() if v["season"] < year}
        wx = {k: v for k, v in wx.items() if k in games}
        tgs = [t for t in tgs if t["season"] < year]
    if not wx:
        sys.exit("No weather rows. Run: python -m backtest.collect")
    with open(os.path.join(HERE, "stories.json")) as f:
        stories = json.load(f)
    res = analyze(games, wx, tgs, stories)
    with open(os.path.join(args.out, "report.md"), "w") as f:
        f.write(report_md(res))
    with open(os.path.join(args.out, "results.json"), "w") as f:
        json.dump(res, f, indent=1, default=str)
    with open(os.path.join(args.out, "calibration.json"), "w") as f:
        json.dump(calibration(res), f, indent=1)
    print(f"{res['meta']['n_games']} games analyzed. Wrote report.md, results.json, calibration.json to {args.out}")
    from backtest import hfa
    hfa.main()


if __name__ == "__main__":
    main()
