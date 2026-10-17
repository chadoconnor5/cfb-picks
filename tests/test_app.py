import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

from grading import format_spread, grade_ats, grade_ou  # noqa: E402


# ---------- grading ----------

@pytest.mark.parametrize("side,spread,hp,ap,expected", [
    ("home", -7, 28, 20, "W"),    # home -7 wins by 8
    ("home", -7, 27, 20, "P"),    # wins by exactly 7
    ("home", -7, 26, 20, "L"),
    ("away", -7, 26, 20, "W"),    # away +7 covers
    ("away", 3.5, 20, 24, "W"),   # home is +3.5 dog, loses by 4 -> away covers
    ("home", 3.5, 20, 23, "W"),   # home +3.5 loses by 3 -> covers
    ("home", None, 20, 23, None),
    (None, -3, 20, 23, None),
])
def test_grade_ats(side, spread, hp, ap, expected):
    assert grade_ats(side, spread, hp, ap) == expected


@pytest.mark.parametrize("side,total,hp,ap,expected", [
    ("over", 50.5, 28, 24, "W"),
    ("under", 50.5, 28, 24, "L"),
    ("over", 52, 28, 24, "P"),
    ("under", 52, 21, 24, "W"),
    ("over", None, 21, 24, None),
])
def test_grade_ou(side, total, hp, ap, expected):
    assert grade_ou(side, total, hp, ap) == expected


def test_format_spread():
    assert format_spread("Ohio State", -7) == "Ohio State -7"
    assert format_spread("Purdue", 7.5) == "Purdue +7.5"
    assert format_spread("Army", 0) == "Army PK"


# ---------- CFBD parsing ----------

def test_pick_line_prefers_provider():
    import cfbd
    lines = [
        {"provider": "Bovada", "spread": "-6.5", "overUnder": "51"},
        {"provider": "DraftKings", "spread": -7, "overUnder": 50.5, "spreadOpen": -6, "overUnderOpen": 52},
    ]
    assert cfbd.pick_line(lines, "draftkings") == {
        "provider": "DraftKings", "spread": -7.0, "total": 50.5, "spread_open": -6.0, "total_open": 52.0,
    }
    assert cfbd.pick_line(lines, None)["provider"] == "Bovada"
    assert cfbd.pick_line([{"provider": "X", "spread": None}], None) is None


# ---------- API flow (CFBD + Claude mocked) ----------

FUTURE = "2099-10-18T19:30:00.000Z"
PAST = "2000-10-18T19:30:00.000Z"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CFB_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("BACKTEST_DB_PATH", str(tmp_path / "hist.db"))
    for mod in ("app", "db", "history", "backtest.collect", "pool_api", "league_api"):
        sys.modules.pop(mod, None)
    from backtest import collect as bt_collect

    def fake_collect_season(conn, season):
        # Two past meetings with closing lines + box stats.
        for gid, yr, hp, ap, spread, total in ((901, 2024, 20, 13, -3.5, 45.5), (902, 2025, 14, 17, -6.5, 47.0)):
            conn.execute("""INSERT OR REPLACE INTO bt_games (id, season, week, season_type, start_date, start_tbd, home_team,
                            away_team, home_points, away_points, neutral_site, venue_id, spread_close, total_close,
                            conference_game) VALUES (?, ?, 10, 'regular', ?, 0, 'Ohio State', 'Penn State', ?, ?, 0, 10, ?, ?, 1)""",
                         (gid, yr, f"{yr}-11-02T17:00:00Z", hp, ap, spread, total))
            for team, opp, pts, rush in (("Ohio State", "Penn State", hp, 180), ("Penn State", "Ohio State", ap, 95)):
                conn.execute("""INSERT OR REPLACE INTO bt_team_games (game_id, team, opponent, points, rush_yds, total_yds, sacks)
                                VALUES (?, ?, ?, ?, ?, ?, ?)""", (gid, team, opp, pts, rush, rush + 200, 2))
        conn.commit()
    monkeypatch.setattr(bt_collect, "collect_season", fake_collect_season)
    monkeypatch.setattr(bt_collect, "collect_context", lambda conn, season: [])
    monkeypatch.setattr(bt_collect, "collect_venues", lambda conn: None)
    import app as app_module
    import cfbd

    games = [
        {"id": 1, "season": 2026, "week": 8, "season_type": "regular", "start_date": FUTURE,
         "home_team": "Ohio State", "away_team": "Penn State", "home_conf": "Big Ten", "away_conf": "Big Ten",
         "neutral_site": 0, "venue": "Ohio Stadium", "venue_id": 10, "home_points": None, "away_points": None, "completed": 0},
        {"id": 2, "season": 2026, "week": 8, "season_type": "regular", "start_date": FUTURE,
         "home_team": "Texas", "away_team": "Oklahoma", "home_conf": "SEC", "away_conf": "SEC",
         "neutral_site": 1, "venue": "Cotton Bowl", "venue_id": 20, "home_points": None, "away_points": None, "completed": 0},
    ]
    lines = {
        1: {"provider": "DraftKings", "spread": -7.0, "total": 50.5, "spread_open": -6.0, "total_open": 51.0},
        2: {"provider": "DraftKings", "spread": -3.0, "total": 55.5, "spread_open": None, "total_open": None},
    }
    state = {"games": games, "lines": lines}
    monkeypatch.setattr(cfbd, "fetch_games", lambda *a, **k: [dict(g) for g in state["games"]])
    monkeypatch.setattr(cfbd, "fetch_lines", lambda *a, **k: state["lines"])

    import analytics
    import injuries
    import teams as teams_mod
    ref = teams_mod.load_reference(2026)
    monkeypatch.setattr(teams_mod, "fetch_fbs_teams", lambda season: [
        {"school": t, "conference": c} for c, ts in ref["conferences"].items() for t in ts])
    import predict
    import weather
    from fixtures import ADVANCED, SP, STATS

    # Rename two synthetic teams to the slate's teams so profiles attach.
    adv = [dict(a) for a in ADVANCED]
    adv[0]["team"], adv[1]["team"] = "Penn State", "Ohio State"
    monkeypatch.setattr(analytics, "fetch_profiles", lambda season: analytics.build_profiles(adv, SP, STATS))
    monkeypatch.setattr(weather, "fetch_venues", lambda: {
        10: {"id": 10, "name": "Ohio Stadium", "latitude": 40.0, "longitude": -83.0, "elevation": 230, "dome": 0, "grass": 1},
        20: {"id": 20, "name": "Cotton Bowl", "latitude": 32.8, "longitude": -96.8, "elevation": 140, "dome": 1, "grass": 1},
    })
    monkeypatch.setattr(weather, "fetch_forecast", lambda lat, lon, start: {
        "temp_f": 41, "temp_min_f": 39, "temp_max_f": 42, "wind_mph": 22, "gust_mph": 36,
        "precip_in": 0.5, "precip_max_hr_in": 0.2, "precip_prob": 90, "snow_in": 0, "weather_code": 63})
    calls = {"injuries": 0, "contexts": []}

    def fake_injuries(game):
        calls["injuries"] += 1
        return {"report": "Penn State: WR1 out.", "sources": [{"url": "https://example.com/r", "title": "Report"}]}
    monkeypatch.setattr(injuries, "fetch_report", fake_injuries)

    def fake_predict(game, notes="", context=None):
        calls["contexts"].append(context)
        return {
            "game_id": game["id"], "model": "test-model", "spread": game["spread"], "total": game["total"],
            "notes": notes, "ats_pick": "home", "ou_pick": "over", "proj_home": 31, "proj_away": 21,
            "confidence": 64, "reasoning": "Test reasoning.", "key_factors": json.dumps(["a", "b"]),
            "conditions_impact": "Wind hurts Penn State's passing game.", "context": json.dumps(context or {}),
        }
    monkeypatch.setattr(predict, "predict", fake_predict)

    c = app_module.app.test_client()
    c.state = state
    c.calls = calls
    return c


WEEK = {"season": 2026, "week": 8, "season_type": "regular"}


def test_full_flow(client):
    r = client.post("/api/sync", json=WEEK)
    assert r.status_code == 200 and r.json["games"] == 2 and r.json["with_lines"] == 2

    games = client.get("/api/games", query_string=WEEK).json
    assert [g["spread"] for g in games] == [-7.0, -3.0]

    # Claude predicts game 1 with notes
    r = client.post("/api/games/1/predict", json={"notes": "QB questionable"})
    assert r.status_code == 200 and r.json["ats_pick"] == "home" and r.json["key_factors"] == ["a", "b"]

    # I fade Claude on ATS, agree on O/U, at a line I got elsewhere (-6.5)
    r = client.post("/api/games/1/pick", json={"ats_pick": "away", "ou_pick": "over", "spread": "-6.5", "total": "50.5"})
    assert r.status_code == 200 and r.json["locked_prediction_id"]

    # Claude can no longer re-predict; duplicate pick rejected
    assert client.post("/api/games/1/predict", json={}).status_code == 400
    assert client.post("/api/games/1/pick", json={"ats_pick": "home", "spread": -7}).status_code == 400

    # Game 2: O/U only, no Claude pick
    assert client.post("/api/games/2/pick", json={"ou_pick": "under", "total": 55.5}).status_code == 200

    # Line moves after my pick; my locked line must not change
    client.state["lines"][1]["spread"] = -9.0
    client.post("/api/sync", json=WEEK)
    g1 = client.get("/api/games", query_string=WEEK).json[0]
    assert g1["spread"] == -9.0 and g1["pick"]["spread"] == -6.5 and g1["prediction"]["locked"] == 1

    # Games finish: OSU 27-20 (wins by 7), Texas 24-21 (45 points)
    for g, (hp, ap) in zip(client.state["games"], [(27, 20), (24, 21)]):
        g.update(home_points=hp, away_points=ap, completed=1, start_date=PAST)
    r = client.post("/api/grade", json=WEEK)
    assert r.json == {"completed": 2, "graded": 2}

    games = client.get("/api/games", query_string=WEEK).json
    g1, g2 = games
    # Me: away +6.5, home won by 7 -> L. Over 50.5 with 47 -> L
    assert (g1["pick"]["ats_result"], g1["pick"]["ou_result"]) == ("L", "L")
    # Claude: home -7 (its own line) won by 7 -> P. Over 50.5 -> L
    assert (g1["prediction"]["ats_result"], g1["prediction"]["ou_result"]) == ("P", "L")
    assert (g2["pick"]["ats_result"], g2["pick"]["ou_result"]) == (None, "W")

    rec = client.get("/api/record?season=2026").json
    assert rec["me"]["ats"] == {"W": 0, "L": 1, "P": 0, "pct": 0.0}
    assert rec["me"]["ou"] == {"W": 1, "L": 1, "P": 0, "pct": 50.0}
    assert rec["claude_locked"]["ats"]["P"] == 1
    assert rec["fade"]["ats"]["L"] == 1 and rec["agree"]["ou"]["L"] == 1
    assert rec["confidence"][1]["ats"]["P"] == 1  # 64 -> 60-69 bucket
    assert len(rec["history"]) == 2

    # Can't delete picks after kickoff
    assert client.delete("/api/games/1/pick").status_code == 400


def test_pick_validation_and_delete(client):
    client.post("/api/sync", json=WEEK)
    assert client.post("/api/games/1/pick", json={}).status_code == 400
    assert client.post("/api/games/1/pick", json={"ats_pick": "home", "spread": ""}).status_code == 400
    assert client.post("/api/games/1/pick", json={"ats_pick": "sideways", "spread": -7}).status_code == 400

    client.post("/api/games/1/predict", json={})
    client.post("/api/games/1/pick", json={"ats_pick": "home", "spread": -7})
    assert client.delete("/api/games/1/pick").status_code == 200
    g1 = client.get("/api/games", query_string=WEEK).json[0]
    assert g1["pick"] is None and g1["prediction"]["locked"] == 0
    assert client.post("/api/games/1/predict", json={}).status_code == 200


def test_prompt_contains_line_and_notes():
    from predict import build_prompt
    game = {"id": 1, "season": 2026, "week": 8, "season_type": "regular", "start_date": FUTURE,
            "home_team": "Ohio State", "away_team": "Penn State", "home_conf": "Big Ten", "away_conf": "Big Ten",
            "neutral_site": 0, "venue": "Ohio Stadium", "spread": -7.0, "total": 50.5,
            "spread_open": None, "total_open": None}
    p = build_prompt(game, "Rain expected")
    assert "Ohio State -7" in p and "Penn State +7" in p and "Total: 50.5" in p and "Rain expected" in p


def test_weather_analytics_injuries_flow(client):
    r = client.post("/api/sync", json=WEEK).json
    assert r["forecasts"] == 1 and r["warnings"] == []   # game 2 is in a dome

    g1, g2 = client.get("/api/games", query_string=WEEK).json
    assert g1["weather"]["wind_mph"] == 22 and g1["assessment"]["conditions"]["level"] == "extreme"
    assert g1["assessment"]["teams"]["away"]["label"] == "exposed"     # Penn State = air raid profile
    assert g1["assessment"]["teams"]["home"]["label"] == "insulated"   # Ohio State = option profile
    assert g1["profiles"]["away"]["style"] == "pass-heavy"
    h = g1["history"]
    assert len(h["h2h"]) == 2 and h["home"]["five_years"]["all"]["ats"] == {"w": 1, "l": 1, "p": 0, "pct": 50.0}
    assert g2["weather"] is None and g2["assessment"]["conditions"]["dome"] is True

    # Predict with injury search: fetches once, then reuses the fresh report
    p = client.post("/api/games/1/predict", json={"search_injuries": True}).json
    assert p["conditions_impact"].startswith("Wind") and "context" not in p
    client.post("/api/games/1/predict", json={"search_injuries": True})
    assert client.calls["injuries"] == 1
    ctx = client.calls["contexts"][-1]
    assert ctx["injuries"]["report"] == "Penn State: WR1 out."
    assert ctx["assessment"]["edge"] == "home" and ctx["home_profile"]["style"] == "run-heavy"
    from predict import build_prompt
    prompt = build_prompt(dict(client.get("/api/games", query_string=WEEK).json[0]), "", ctx)
    assert "Betting history" in prompt and "Recent meetings" in prompt

    # Without the flag, no new search; manual refresh always searches
    client.post("/api/games/1/predict", json={})
    assert client.calls["injuries"] == 1
    inj = client.post("/api/games/1/injuries").json
    assert client.calls["injuries"] == 2 and inj["sources"][0]["url"] == "https://example.com/r"
    g1 = client.get("/api/games", query_string=WEEK).json[0]
    assert g1["injuries"]["report"] == "Penn State: WR1 out."


def test_migrates_old_database(tmp_path, monkeypatch):
    import sqlite3
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE games (id INTEGER PRIMARY KEY, season INTEGER NOT NULL, week INTEGER NOT NULL,
            season_type TEXT NOT NULL, start_date TEXT, home_team TEXT NOT NULL, away_team TEXT NOT NULL,
            home_conf TEXT, away_conf TEXT, neutral_site INTEGER DEFAULT 0, venue TEXT, home_points INTEGER,
            away_points INTEGER, completed INTEGER DEFAULT 0, spread REAL, total REAL, spread_open REAL,
            total_open REAL, provider TEXT, lines_updated_at TEXT, notes TEXT DEFAULT '');
        CREATE TABLE predictions (id INTEGER PRIMARY KEY AUTOINCREMENT, game_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')), model TEXT NOT NULL, spread REAL, total REAL,
            notes TEXT, ats_pick TEXT NOT NULL, ou_pick TEXT NOT NULL, proj_home INTEGER, proj_away INTEGER,
            confidence INTEGER, reasoning TEXT, key_factors TEXT, locked INTEGER DEFAULT 0, ats_result TEXT,
            ou_result TEXT);
    """)
    conn.close()
    monkeypatch.setenv("CFB_DB_PATH", str(path))
    sys.modules.pop("db", None)
    import db
    db.init_db()
    conn = sqlite3.connect(path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(games)")}
    assert {"venue_id", "weather"} <= cols
    assert "context" in {r[1] for r in conn.execute("PRAGMA table_info(predictions)")}
