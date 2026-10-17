import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import teams  # noqa: E402

REF = teams.load_reference(2026)
CFBD_CONF = {"American": "American Athletic", "FBS Independents": "FBS Independents"}
CFBD_NAMES = {"Hawai'i": "Hawaii", "San José State": "San Jose State", "UMass": "Massachusetts", "UConn": "Connecticut"}


def cfbd_style_list():
    """The reference rewritten the way CFBD might spell things."""
    out = []
    for conf, ts in REF["conferences"].items():
        for t in ts:
            out.append({"school": CFBD_NAMES.get(t, t), "conference": CFBD_CONF.get(conf, conf)})
    return out


def test_reference_is_complete():
    names = [t for ts in REF["conferences"].values() for t in ts]
    assert len(names) == len(set(names)) == REF["total"] == 138
    assert "North Dakota State" in REF["conferences"]["Mountain West"]
    assert "Sacramento State" in REF["conferences"]["Mid-American"]
    assert "Texas State" in REF["conferences"]["Pac-12"] and "Louisiana Tech" in REF["conferences"]["Sun Belt"]
    assert "Northern Illinois" in REF["conferences"]["Mountain West"] and "UTEP" in REF["conferences"]["Mountain West"]


def test_verify_accepts_cfbd_spellings():
    r = teams.verify(cfbd_style_list(), REF)
    assert r["ok"], r


def test_verify_flags_differences():
    lst = [t for t in cfbd_style_list() if t["school"] != "North Dakota State"]
    lst.append({"school": "Idaho", "conference": "Big Sky"})
    for t in lst:
        if t["school"] == "UTEP":
            t["conference"] = "Conference USA"   # stale conference
    r = teams.verify(lst, REF)
    assert not r["ok"]
    assert r["missing"] == ["North Dakota State"]
    assert r["unexpected"] == ["Idaho"]
    assert r["conference_mismatches"] == [{"team": "UTEP", "cfbd": "Conference USA", "reference": "Mountain West"}]


def test_program_notes_and_overrides():
    assert "First FBS season" in teams.program_note("North Dakota State", 2026)
    assert teams.program_note("Ohio State", 2026) is None
    assert teams.home_venue_override("North Dakota State")["dome"] is True


def test_parse_teams():
    raw = [{"school": "North Dakota State", "conference": "Mountain West", "classification": "fbs",
            "location": {"name": "Fargodome", "latitude": 46.9, "longitude": -96.8, "timezone": "America/Chicago",
                         "elevation": "274", "capacity": 18700, "dome": True}}]
    t = teams.parse_teams(raw)[0]
    assert t["venue"] == "Fargodome" and t["dome"] is True and t["elevation"] == 274.0


def test_ndsu_home_game_ignores_weather(tmp_path, monkeypatch):
    monkeypatch.setenv("CFB_DB_PATH", str(tmp_path / "t.db"))
    for m in ("app", "db", "history", "pool_api", "league_api"):
        sys.modules.pop(m, None)
    import app
    import db
    storm = {"temp_f": 15, "temp_min_f": 12, "temp_max_f": 16, "wind_mph": 28, "gust_mph": 45,
             "precip_in": 0, "precip_max_hr_in": 0, "precip_prob": 90, "snow_in": 3}
    with db.tx() as conn:
        for gid, neutral in ((1, 0), (2, 1)):
            db.upsert_game(conn, {"id": gid, "season": 2026, "week": 9, "season_type": "regular",
                                  "start_date": "2099-10-24T19:00:00Z", "home_team": "North Dakota State",
                                  "away_team": "Wyoming", "home_conf": "Mountain West", "away_conf": "Mountain West",
                                  "neutral_site": neutral, "venue": None, "venue_id": None,
                                  "home_points": None, "away_points": None, "completed": 0})
            conn.execute("UPDATE games SET weather=? WHERE id=?", (json.dumps(storm), gid))
        home = app.build_context(conn, conn.execute("SELECT * FROM games WHERE id=1").fetchone())
        neutral = app.build_context(conn, conn.execute("SELECT * FROM games WHERE id=2").fetchone())
    assert home["assessment"]["conditions"]["dome"] is True
    assert "First FBS season" in home["program_notes"]["North Dakota State"]
    assert neutral["assessment"]["conditions"]["dome"] is False   # override applies to home games only


def test_teams_endpoint_falls_back_to_reference(tmp_path, monkeypatch):
    monkeypatch.setenv("CFB_DB_PATH", str(tmp_path / "t.db"))
    for m in ("app", "db", "history", "pool_api", "league_api"):
        sys.modules.pop(m, None)
    import app
    r = app.app.test_client().get("/api/teams?season=2026").json
    assert r["source"] == "reference" and r["count"] == 138
    assert any(t["school"] == "North Dakota State" and t["note"] for t in r["conferences"]["Mountain West"])
