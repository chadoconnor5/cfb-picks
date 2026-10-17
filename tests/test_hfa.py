import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from backtest import hfa  # noqa: E402


def world(seed=3):
    """Planted: 'Fortress' covers +5 at home; crowd above normal helps (+1.5 pts per 10 fill pts);
    rain cuts turnout by 15 fill pts; one-off lucky team 'Fluke' has only 3 home games."""
    rnd = random.Random(seed)
    teams = [f"T{i}" for i in range(30)] + ["Fortress"]
    rows = []
    gid = 0
    for season in (2023, 2024, 2025):
        for wk in range(12):
            rnd.shuffle(teams)
            for h, a in zip(teams[::2], teams[1::2]):
                gid += 1
                rain = rnd.random() < 0.15
                normal = 0.9 if h == "Fortress" else 0.75
                fill = max(0.2, min(1.05, normal + rnd.gauss(0, 0.08) - (0.15 if rain else 0)))
                cover = rnd.gauss(0, 13) + (5 if h == "Fortress" else 0) + 1.5 * (fill - normal) * 10
                hp = 28
                rows.append({"id": gid, "season": season, "home_team": h, "away_team": a, "neutral_site": 0,
                             "home_points": hp, "away_points": hp, "spread_close": cover, "cover": cover,
                             "attendance": int(fill * 60000), "capacity": 60000, "fill": fill,
                             "wx": {"wind_mph": 5, "precip_in": 0.4 if rain else 0, "snow_in": 0, "temp_min_f": 55, "temp_max_f": 60}})
    for i in range(3):
        rows.append({"id": 9000 + i, "season": 2025, "home_team": "Fluke", "away_team": "T1", "neutral_site": 0,
                     "home_points": 30, "away_points": 10, "spread_close": 0, "cover": 20, "attendance": 30000,
                     "capacity": 60000, "fill": 0.5, "wx": None})
    return rows


def test_hfa_recovers_planted_effects():
    res = hfa.analyze_hfa(world())
    fort = res["teams"]["Fortress"]
    assert fort["home_cover_margin"] > 3 and 0 < fort["home_edge_shrunk"] < fort["home_cover_margin"]
    fluke = res["teams"]["Fluke"]
    assert fluke["home_cover_margin"] == 20 and fluke["home_edge_shrunk"] < 5     # 3 games: heavily shrunk
    crowd = res["crowd_regression"]["coef"]["fill_vs_normal_per_10pts"]
    assert crowd["b"] > 0.5 and crowd["t"] > 2
    rain = res["weather_attendance"]["coef"]["rain_in"]
    assert rain["b"] < -20 and rain["t"] < -2                                         # ~ -15 pts per 0.4 in
    assert set(res["fill_buckets"]) == {"<60%", "60-80%", "80-95%", "95%+"}


def test_expected_fill_and_describe():
    res = hfa.analyze_hfa(world())
    dry = {"wind_mph": 5, "precip_in": 0, "snow_in": 0, "temp_min_f": 55, "temp_max_f": 60}
    wet = {**dry, "precip_in": 0.5}
    t = res["teams"]["Fortress"]
    assert hfa.expected_fill(t, res["weather_attendance"], wet) < hfa.expected_fill(t, res["weather_attendance"], dry)
    text = hfa.describe(res, "Fortress", "T1", neutral=False, w=wet, capacity=60000)
    assert "Fortress at home" in text and "Expected crowd" in text and "fans" in text
    assert "Neutral site" in hfa.describe(res, "Fortress", "T1", neutral=True)
