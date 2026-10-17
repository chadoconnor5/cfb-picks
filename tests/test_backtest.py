"""End-to-end backtest on a synthetic world with a known, planted weather effect.

Truth planted below: every mph of wind above 10 costs offense EPA, pass-heavy offenses lose about
3x as much as run-heavy ones, teams throw less, and the closing total only prices half of the
scoring drop. The pipeline must recover all of that from raw CFBD/Open-Meteo-shaped responses.
"""
import json
import os
import random
import sys
from datetime import date, datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

N_TEAMS, WEEKS, SEASONS = 40, 11, (2022, 2023)


def build_world(seed=7):
    rnd = random.Random(seed)
    teams = [{"name": f"Team {i:02d}", "venue": 100 + i, "lat": 30 + i * 0.3, "lon": -90 - i * 0.2,
              "pass_rate": 0.30 + 0.35 * i / (N_TEAMS - 1), "eff": rnd.gauss(0.1, 0.08)} for i in range(N_TEAMS)]
    games, box, adv, lines, wx = {}, {}, {}, {}, {}
    gid = 1
    for season in SEASONS:
        start = date(season, 9, 3)
        for week in range(1, WEEKS + 1):
            order = teams[:]
            rnd.shuffle(order)
            day = start + timedelta(days=7 * (week - 1))
            for h, a in zip(order[::2], order[1::2]):
                r = rnd.random()
                wind = rnd.uniform(18, 30) if r < 0.18 else rnd.uniform(11, 17) if r < 0.35 else rnd.uniform(2, 9)
                rain = rnd.uniform(0.3, 0.8) if rnd.random() < 0.08 else 0.0
                wx[(round(h["lat"], 4), day.isoformat())] = {"wind": wind, "rain": rain}
                over10 = max(0.0, wind - 10)
                pts, total_exp, rows = {}, 0, []
                for t, opp, home in ((h, a, 1), (a, h, 0)):
                    pr_c = (t["pass_rate"] - 0.475) / 0.175            # -1 (run) .. +1 (pass)
                    hit = over10 * 0.010 * (1 + pr_c) + rain * 0.10 * (1 + 0.5 * pr_c)
                    off = t["eff"] - 0.5 * opp["eff"] + 0.05 * home + rnd.gauss(0, 0.12) - hit
                    pass_rate = max(0.15, t["pass_rate"] - 0.004 * over10 + rnd.gauss(0, 0.03))
                    plays = 70
                    p_att = round(plays * pass_rate)
                    points = max(0, round(24 + 45 * off))
                    pts[t["name"]] = points
                    total_exp += 24 + 45 * (t["eff"] - 0.5 * opp["eff"] + 0.05 * home - hit / 2)  # market prices half
                    rows.append((t, opp, home, off, p_att, plays - p_att, points, pr_c, hit))
                games[gid] = {"id": gid, "season": season, "week": week, "seasonType": "regular",
                              "startDate": f"{day.isoformat()}T19:00:00.000Z", "startTimeTBD": False,
                              "neutralSite": False, "venueId": h["venue"], "homeTeam": h["name"], "awayTeam": a["name"],
                              "homePoints": pts[h["name"]], "awayPoints": pts[a["name"]], "completed": True}
                lines[gid] = {"id": gid, "lines": [
                    {"provider": "A", "spread": -(0.0) , "overUnder": round(total_exp * 2) / 2},
                    {"provider": "B", "spread": 0.0, "overUnder": round(total_exp * 2) / 2 + 0.5}]}
                for t, opp, home, off, p_att, r_att, points, pr_c, hit in rows:
                    pass_ppa = off + 0.05 * pr_c - 0.3 * hit * pr_c
                    rush_ppa = off - 0.05
                    key = (season, week)
                    box.setdefault(key, {}).setdefault(gid, {"id": gid, "teams": []})["teams"].append(
                        {"team": t["name"], "homeAway": "home" if home else "away", "points": points,
                         "stats": [{"category": "completionAttempts", "stat": f"{int(p_att * .6)}-{p_att}"},
                                   {"category": "rushingAttempts", "stat": str(r_att)},
                                   {"category": "turnovers", "stat": "1"}]})
                    adv.setdefault(key, []).append({
                        "gameId": gid, "team": t["name"], "opponent": opp["name"],
                        "offense": {"ppa": off, "successRate": 0.42 + off / 4, "explosiveness": 1.2,
                                    "passingPlays": {"ppa": pass_ppa, "successRate": 0.42, "explosiveness": 1.3 + 0.3 * pr_c},
                                    "rushingPlays": {"ppa": rush_ppa, "successRate": 0.42, "explosiveness": 1.0}},
                        "defense": {"ppa": 0.1, "passingPlays": {"ppa": 0.1}, "rushingPlays": {"ppa": 0.1 - 0.2 * opp["eff"]}}})
                gid += 1
    venues = [{"id": t["venue"], "name": f"Stadium {t['name']}", "latitude": t["lat"], "longitude": t["lon"], "dome": False}
              for t in teams]
    return {"games": games, "box": box, "adv": adv, "lines": lines, "wx": wx, "venues": venues}


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("bt")
    os.environ["BACKTEST_DB_PATH"] = str(tmp / "bt.db")
    for m in ("backtest.collect", "backtest.analyze"):
        sys.modules.pop(m, None)
    from backtest import analyze, collect

    world = build_world()

    def fake_cfbd(path, **p):
        if path == "/venues":
            return world["venues"]
        if path == "/games":
            return [g for g in world["games"].values() if g["season"] == p["year"] and p["seasonType"] == "regular"]
        if path == "/lines":
            return [l for gid, l in world["lines"].items() if world["games"][gid]["season"] == p["year"] and p["seasonType"] == "regular"]
        if path == "/games/teams":
            return list(world["box"].get((p["year"], p["week"]), {}).values())
        if path == "/stats/game/advanced":
            return world["adv"].get((p["year"], p["week"]), [])
        raise AssertionError(path)

    def fake_archive(lat, lon, start, end):
        d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
        hourly = {k: [] for k in ("time", "temperature_2m", "wind_speed_10m", "wind_gusts_10m", "precipitation", "snowfall", "weather_code")}
        d = d0
        while d <= d1:
            w = world["wx"].get((round(lat, 4), d.isoformat()), {"wind": 4, "rain": 0})
            for hr in range(24):
                hourly["time"].append(datetime(d.year, d.month, d.day, hr).strftime("%Y-%m-%dT%H:%M"))
                hourly["temperature_2m"].append(60)
                hourly["wind_speed_10m"].append(w["wind"])
                hourly["wind_gusts_10m"].append(w["wind"] * 1.5)
                hourly["precipitation"].append(w["rain"] / 4)
                hourly["snowfall"].append(0)
                hourly["weather_code"].append(61 if w["rain"] else 1)
            d += timedelta(days=1)
        return {"hourly": hourly}

    collect.cached_cfbd = fake_cfbd
    collect.cached_archive = fake_archive
    collect.main(["--seasons", "2022-2023"])

    conn = collect.connect()
    games, wx, tgs = analyze.load(conn)
    g = next(iter(world["games"].values()))
    stories = [{"season": g["season"], "date": g["startDate"][:10], "home": g["homeTeam"], "away": g["awayTeam"],
                "reported": "test story"},
               {"season": 2022, "home": "Nobody", "away": "Nowhere", "reported": "missing"}]
    res = analyze.analyze(games, wx, tgs, stories)
    res["_md"] = analyze.report_md(res)
    res["_cal"] = analyze.calibration(res)
    return res


def test_collected_everything(results):
    assert results["meta"]["n_games"] == len(SEASONS) * WEEKS * N_TEAMS // 2
    assert results["meta"]["n_team_games"] == 2 * results["meta"]["n_games"]


def test_recovers_style_by_wind(results):
    sev = results["style_by_wind"]["severe"]
    assert sev["pass-heavy"]["d_off_ppa"] < sev["run-heavy"]["d_off_ppa"] < 0
    assert sev["pass-heavy"]["d_pass_rate"] < 0
    calm = results["style_by_wind"]["none"]
    assert calm["pass-heavy"]["d_off_ppa"] > sev["pass-heavy"]["d_off_ppa"]


def test_recovers_market_underpricing(results):
    assert results["wind"]["severe"]["under"]["pct"] > 60
    assert results["wind"]["severe"]["pts_vs_total"] < 0
    assert abs(results["wind"]["none"]["pts_vs_total"]) < abs(results["wind"]["severe"]["pts_vs_total"])


def test_regression_finds_interaction(results):
    reg = results["regression"]["team_off_epa_delta"]["coef"]
    assert reg["wind_mph"]["b"] < 0 and reg["wind_mph"]["t"] < -2
    assert reg["wind_x_pass_heavy"]["b"] < 0 and reg["wind_x_pass_heavy"]["t"] < -2
    assert results["regression"]["team_pass_rate_delta"]["coef"]["wind_mph"]["b"] < 0


def test_rules_point_the_right_way(results):
    labels = results["rules"]["labels"]
    assert labels["exposed"]["d_off_ppa"] < labels["insulated"]["d_off_ppa"]
    assert results["rules"]["under_lean"]["under"]["pct"] > 55


def test_stories_and_outputs(results):
    matched, missing = results["stories"]
    assert matched["matched"] and missing["matched"] is None
    md = results["_md"]
    assert "Style × wind" in md and "not found in data" in md and "Do the app's rules work?" in md
    cal = results["_cal"]
    assert cal["wind"]["severe"]["by_style"]["pass-heavy"]["n"] > 0


def test_calibration_reaches_prompt(results):
    import conditions
    cal = json.loads(json.dumps(results["_cal"]))  # as loaded from disk
    pass_heavy = {"team": "Air", "style": "pass-heavy", "off": {"pass_rate": 0.62},
                  "pct": {"pass_rate": 95, "pass_expl": 90, "rush_ppa": 20, "pass_ppa": 80, "def_rush_ppa": 50}}
    run_heavy = {"team": "Ground", "style": "run-heavy", "off": {"pass_rate": 0.25},
                 "pct": {"pass_rate": 5, "pass_expl": 10, "rush_ppa": 80, "pass_ppa": 20, "def_rush_ppa": 50}}
    w = {"temp_f": 50, "temp_min_f": 50, "temp_max_f": 52, "wind_mph": 24, "gust_mph": 36,
         "precip_in": 0, "precip_max_hr_in": 0, "precip_prob": 0, "snow_in": 0}
    c = conditions.classify(w)
    a = conditions.assess(run_heavy, pass_heavy, w, c, "Ground", "Air", cal)
    text = conditions.describe(a)
    assert "Historical evidence" in text and "Past games with severe wind" in text
    assert "Air: pass-heavy offenses in" in text
