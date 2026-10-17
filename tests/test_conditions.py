import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

import analytics  # noqa: E402
import conditions  # noqa: E402
import weather  # noqa: E402
from fixtures import ADVANCED, SP, STATS  # noqa: E402

PROFILES = analytics.build_profiles(ADVANCED, SP, STATS)
CALM = {"temp_f": 68, "temp_min_f": 66, "temp_max_f": 70, "wind_mph": 6, "gust_mph": 11,
        "precip_in": 0, "precip_max_hr_in": 0, "precip_prob": 5, "snow_in": 0}
STORM = {"temp_f": 45, "temp_min_f": 43, "temp_max_f": 46, "wind_mph": 21, "gust_mph": 34,
         "precip_in": 0.6, "precip_max_hr_in": 0.25, "precip_prob": 95, "snow_in": 0}
SNOW = {**CALM, "temp_f": 24, "temp_min_f": 18, "temp_max_f": 26, "wind_mph": 9, "gust_mph": 15, "snow_in": 2.5}


def test_profiles_styles_and_percentiles():
    air, option = PROFILES["Air Raid U"], PROFILES["Option Tech"]
    assert air["style"] == "pass-heavy" and option["style"] == "run-heavy"
    assert air["pct"]["pass_rate"] > 90 and option["pct"]["pass_rate"] < 10
    assert air["plays_per_game"] == 100.0
    assert air["sp"]["rating"] == 12.5
    assert PROFILES["Wall State"]["pct"]["def_rush_ppa"] > 90  # lowest EPA allowed -> top pct
    text = analytics.describe(air)
    assert "pass-heavy" in text and "SP+ +12.5" in text


def test_classify_levels():
    assert conditions.classify(CALM)["level"] == "none"
    storm = conditions.classify(STORM)
    assert storm["wind"] == "severe" and storm["precip"] == "moderate" and storm["level"] == "extreme"
    snow = conditions.classify(SNOW)
    assert snow["precip_type"] == "snow" and snow["precip"] == "severe" and snow["temp"] == "severe"
    assert conditions.classify(STORM, dome=True)["level"] == "none"
    assert conditions.classify(None) is None


def test_pass_heavy_exposed_run_heavy_insulated_in_storm():
    a = conditions.assess(PROFILES["Option Tech"], PROFILES["Air Raid U"], STORM, conditions.classify(STORM),
                          "Option Tech", "Air Raid U")
    assert a["teams"]["away"]["label"] == "exposed"     # Air Raid U
    assert a["teams"]["home"]["label"] == "insulated"   # Option Tech
    assert a["edge"] == "home" and a["total_lean"] == "under"
    assert any("weak run game" in r for r in a["teams"]["away"]["reasons"])
    text = conditions.describe(a)
    assert "Air Raid U: pass-heavy, weather exposure exposed" in text


def test_calm_weather_changes_nothing():
    a = conditions.assess(PROFILES["Option Tech"], PROFILES["Air Raid U"], CALM, conditions.classify(CALM),
                          "Option Tech", "Air Raid U")
    assert {t["label"] for t in a["teams"].values()} == {"unaffected"}
    assert a["edge"] is None and a["total_lean"] is None


def test_stout_run_defense_erodes_insulation():
    score_vs_filler, _ = conditions.team_exposure(PROFILES["Option Tech"], PROFILES["Filler 0"])
    score_vs_wall, reasons = conditions.team_exposure(PROFILES["Option Tech"], PROFILES["Wall State"])
    assert score_vs_wall > score_vs_filler
    assert any("stout run defense" in r for r in reasons)


def test_forecast_window():
    hourly = {
        "time": ["2026-10-18T18:00", "2026-10-18T19:00", "2026-10-18T20:00", "2026-10-18T21:00", "2026-10-18T22:00", "2026-10-18T23:00"],
        "temperature_2m": [60, 58, 56, 54, 52, 50],
        "wind_speed_10m": [5, 12, 18, 14, 10, 30],
        "wind_gusts_10m": [9, 20, 29, 22, 15, 45],
        "precipitation": [0, 0.05, 0.12, 0.08, 0.0, 0.5],
        "precipitation_probability": [10, 60, 80, 70, 30, 90],
        "snowfall": [0, 0, 0, 0, 0, 0],
        "weather_code": [1, 61, 63, 61, 3, 65],
    }
    kickoff = datetime(2026, 10, 18, 19, 30, tzinfo=timezone.utc)
    w = weather.forecast_window(hourly, kickoff)
    # Window is 19:00-22:00 (4 hours); the 23:00 storm is excluded.
    assert w["temp_f"] == 58 and w["wind_mph"] == 18 and w["gust_mph"] == 29
    assert w["precip_in"] == 0.25 and w["precip_prob"] == 80
    assert weather.forecast_window(hourly, datetime(2026, 10, 25, tzinfo=timezone.utc)) is None


def test_prompt_includes_context():
    from predict import build_prompt
    game = {"id": 1, "season": 2026, "week": 8, "season_type": "regular", "start_date": "2099-10-18T19:30:00Z",
            "home_team": "Option Tech", "away_team": "Air Raid U", "home_conf": "Test", "away_conf": "Test",
            "neutral_site": 0, "venue": None, "spread": -3.0, "total": 58.5, "spread_open": None, "total_open": None}
    a = conditions.assess(PROFILES["Option Tech"], PROFILES["Air Raid U"], STORM, conditions.classify(STORM),
                          "Option Tech", "Air Raid U")
    p = build_prompt(game, "", {"home_profile": PROFILES["Option Tech"], "away_profile": PROFILES["Air Raid U"],
                                "assessment": a,
                                "injuries": {"report": "Air Raid U: QB out", "fetched_at": "2026-10-17 12:00"}})
    assert "wind 21 mph (gusts 34)" in p and "weather exposure exposed" in p
    assert "Air Raid U: QB out" in p and "run-heavy" in p
