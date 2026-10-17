"""Home field advantage and attendance, measured against closing lines.

    python -m backtest.hfa          (also run by backtest.analyze)

Writes backtest/hfa.json, which the app reads.

- Cover margin = home points + closing spread - away points (from the team's side). Positive means
  the team beat the market's expectation, so this measures home edge the market under-prices.
- Team home edges are shrunk toward 0 (empirical Bayes) so a few lucky covers don't look like a fortress.
- Attendance fill = attendance / capacity. Crowd effect is tested both on raw fill and on fill
  relative to that team's own normal crowd (separating "full stadium" from "good team").
- Weather vs turnout: fill relative to normal, regressed on wind, rain, snow, cold and heat.
"""
import json
import os
import statistics
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.analyze import ols, rate  # noqa: E402
from backtest.collect import connect  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "hfa.json")
FILL_BUCKETS = [(0, 0.6, "<60%"), (0.6, 0.8, "60-80%"), (0.8, 0.95, "80-95%"), (0.95, 9, "95%+")]


def load_rows(conn):
    rows = conn.execute("""
        SELECT g.*, v.capacity, w.data AS wx FROM bt_games g
        LEFT JOIN bt_venues v ON v.id = g.venue_id
        LEFT JOIN bt_weather w ON w.game_id = g.id
        WHERE g.home_points IS NOT NULL AND g.spread_close IS NOT NULL""").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["cover"] = d["home_points"] + d["spread_close"] - d["away_points"]
        cap = d.get("capacity")
        d["fill"] = d["attendance"] / cap if d.get("attendance") and cap and cap > 0 and d["attendance"] <= cap * 1.25 else None
        d["wx"] = json.loads(d["wx"]) if d.get("wx") else None
        out.append(d)
    return out


def shrink(values, sd_noise):
    """Empirical-Bayes shrinkage factor k = noise variance / true-effect variance."""
    means = [statistics.mean(v) for v in values if len(v) >= 5]
    ns = [len(v) for v in values if len(v) >= 5]
    if len(means) < 5:
        return None
    var_means = statistics.pvariance(means)
    avg_sampling = statistics.mean(sd_noise ** 2 / n for n in ns)
    tau2 = max(var_means - avg_sampling, 0.25)
    return sd_noise ** 2 / tau2


def completed_seasons_only(rows):
    """Drop the in-progress season so replays of its weeks can't see its own results."""
    from datetime import datetime
    year = datetime.now().year
    return [r for r in rows if r["season"] < year]


def analyze_hfa(rows):
    home_games = [r for r in rows if not r["neutral_site"]]
    res = {"n_games": len(home_games)}

    by_season = defaultdict(list)
    for r in home_games:
        by_season[r["season"]].append(r)
    res["league_by_season"] = {s: {"n": len(v), "home_cover_margin": round(statistics.mean(x["cover"] for x in v), 2),
                                   "home_ats": rate(["W" if x["cover"] > 0 else "L" if x["cover"] < 0 else "P" for x in v])}
                               for s, v in sorted(by_season.items())}
    res["league"] = {"home_cover_margin": round(statistics.mean(r["cover"] for r in home_games), 2) if home_games else None,
                     "home_ats": rate(["W" if r["cover"] > 0 else "L" if r["cover"] < 0 else "P" for r in home_games])}

    # Team-specific (team perspective: home cover vs away cover)
    home = defaultdict(list)
    away = defaultdict(list)
    fills = defaultdict(list)
    atts = defaultdict(list)
    for r in rows:
        if not r["neutral_site"]:
            home[r["home_team"]].append(r["cover"])
            if r["fill"] is not None:
                fills[r["home_team"]].append(r["fill"])
            if r.get("attendance"):
                atts[r["home_team"]].append(r["attendance"])
            away[r["away_team"]].append(-r["cover"])
    sd = statistics.pstdev([r["cover"] for r in home_games]) if len(home_games) > 1 else 14.0
    k = shrink(list(home.values()), sd) or 50.0
    res["shrinkage_k"] = round(k, 1)
    teams = {}
    for t, v in home.items():
        n = len(v)
        m = statistics.mean(v)
        a = away.get(t, [])
        teams[t] = {"n_home": n, "home_cover_margin": round(m, 2), "home_edge_shrunk": round(m * n / (n + k), 2),
                    "home_ats": rate(["W" if x > 0 else "L" if x < 0 else "P" for x in v]),
                    "n_away": len(a), "away_cover_margin": round(statistics.mean(a), 2) if a else None,
                    "avg_fill": round(statistics.mean(fills[t]), 3) if fills.get(t) else None,
                    "avg_attendance": round(statistics.mean(atts[t])) if atts.get(t) else None}
    res["teams"] = teams

    # Crowd: raw fill buckets, and fill relative to the team's normal
    with_fill = [r for r in home_games if r["fill"] is not None]
    buckets = {}
    for lo, hi, label in FILL_BUCKETS:
        b = [r for r in with_fill if lo <= r["fill"] < hi]
        buckets[label] = {"n": len(b), "home_cover_margin": round(statistics.mean(x["cover"] for x in b), 2) if b else None,
                          "home_ats": rate(["W" if x["cover"] > 0 else "L" if x["cover"] < 0 else "P" for x in b])}
    res["fill_buckets"] = buckets
    # Two separate models (raw fill and fill-vs-normal overlap heavily, so one model would blur both).
    X, Xr, y = [], [], []
    for r in with_fill:
        norm = teams[r["home_team"]]["avg_fill"]
        if norm:
            r["fill_dev"] = r["fill"] - norm
            X.append([1, r["fill_dev"] * 10])   # per 10 percentage points vs that team's normal crowd
            Xr.append([1, r["fill"] * 10])      # per 10 percentage points of raw fill
            y.append(r["cover"])
    res["crowd_regression"] = ols(X, y, ["const", "fill_vs_normal_per_10pts"]) if X else None
    res["raw_fill_regression"] = ols(Xr, y, ["const", "raw_fill_per_10pts"]) if Xr else None

    # Weather vs turnout (fill relative to normal)
    Xw, yw = [], []
    for r in with_fill:
        w = r["wx"]
        if w is None or r.get("fill_dev") is None:
            continue
        snow = w.get("snow_in") or 0
        Xw.append([1, w.get("wind_mph") or 0, max(0.0, (w.get("precip_in") or 0) - snow / 10), snow,
                   1.0 if (w.get("temp_min_f") or 99) <= 32 else 0.0, 1.0 if (w.get("temp_max_f") or 0) >= 90 else 0.0])
        yw.append(r["fill_dev"] * 100)   # percentage points
    res["weather_attendance"] = ols(Xw, yw, ["const", "wind_mph", "rain_in", "snow_in", "cold", "heat"]) if Xw else None
    return res


def expected_fill(team_stats, weather_reg, w):
    """Team's normal fill adjusted by the weather-turnout regression for this forecast."""
    if not team_stats or team_stats.get("avg_fill") is None:
        return None
    adj = 0.0
    if weather_reg and w:
        c = {k: v["b"] for k, v in weather_reg["coef"].items()}
        snow = w.get("snow_in") or 0
        adj = (c.get("wind_mph", 0) * (w.get("wind_mph") or 0)
               + c.get("rain_in", 0) * max(0.0, (w.get("precip_in") or 0) - snow / 10)
               + c.get("snow_in", 0) * snow
               + c.get("cold", 0) * (1 if (w.get("temp_min_f") or 99) <= 32 else 0)
               + c.get("heat", 0) * (1 if (w.get("temp_max_f") or 0) >= 90 else 0)) / 100
    return max(0.0, min(1.1, team_stats["avg_fill"] + adj))


def describe(hfa, home, away, neutral, w=None, capacity=None):
    if not hfa:
        return "  (no home-field data; run python -m backtest.analyze)"
    L = []
    lg = hfa.get("league", {})
    if lg.get("home_cover_margin") is not None:
        L.append(f"  League: home teams average {lg['home_cover_margin']:+.1f} pts vs the closing spread "
                 f"(home ATS {lg['home_ats']['pct']}%), i.e. how much home field the market misses overall.")
    if neutral:
        L.append("  Neutral site: no home crowd edge for either team.")
        return "\n".join(L)
    t = hfa["teams"].get(home)
    if t:
        L.append(f"  {home} at home: {t['home_cover_margin']:+.1f} pts vs spread over {t['n_home']} games "
                 f"(shrunk estimate {t['home_edge_shrunk']:+.1f}), home ATS {t['home_ats']['w']}-{t['home_ats']['l']}.")
        ef = expected_fill(t, hfa.get("weather_attendance"), w)
        if ef is not None:
            crowd = f" (~{round(ef * capacity):,} fans)" if capacity else ""
            L.append(f"  Expected crowd: about {ef * 100:.0f}% full{crowd}; normal {t['avg_fill'] * 100:.0f}%.")
            for lo, hi, label in FILL_BUCKETS:
                if lo <= ef < hi:
                    b = hfa["fill_buckets"].get(label, {})
                    if b.get("n"):
                        L.append(f"  Home teams with {label} crowds: {b['home_cover_margin']:+.1f} pts vs spread, "
                                 f"ATS {b['home_ats']['pct']}% (n={b['n']}).")
    a = hfa["teams"].get(away)
    if a and a.get("away_cover_margin") is not None:
        L.append(f"  {away} on the road: {a['away_cover_margin']:+.1f} pts vs spread over {a['n_away']} games.")
    reg = hfa.get("crowd_regression")
    if reg and "fill_vs_normal_per_10pts" in reg["coef"]:
        c = reg["coef"]["fill_vs_normal_per_10pts"]
        L.append(f"  Crowd vs that team's normal: {c['b']:+.2f} pts vs spread per 10 points of fill (t={c['t']}).")
    return "\n".join(L)


def main():
    conn = connect()
    res = analyze_hfa(completed_seasons_only(load_rows(conn)))
    with open(OUT, "w") as f:
        json.dump(res, f, indent=1)
    print(f"Home-field analysis: {res['n_games']} home games. Wrote {OUT}")


if __name__ == "__main__":
    main()
