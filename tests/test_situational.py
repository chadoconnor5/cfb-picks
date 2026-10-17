import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture
def hconn(tmp_path):
    from backtest import collect
    conn = collect.connect(str(tmp_path / "h.db"))
    venues = [(1, "Ohio Stadium", 40.0017, -83.0197, 0, 102780, 228, 1, "America/New_York"),
              (2, "Gesa Field", 46.7319, -117.1606, 0, 32952, 777, 0, "America/Los_Angeles"),
              (3, "Folsom Field", 40.0092, -105.2669, 0, 50183, 1655, 1, "America/Denver")]
    conn.executemany("INSERT INTO bt_venues VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", venues)
    teams = [(2026, "Ohio State", "Big Ten", "fbs", 40.0017, -83.0197, "America/New_York", 1),
             (2026, "Washington State", "Pac-12", "fbs", 46.7319, -117.1606, "America/Los_Angeles", 2),
             (2026, "Colorado", "Big 12", "fbs", 40.0092, -105.2669, "America/Denver", 3),
             (2026, "Michigan", "Big Ten", "fbs", 42.2658, -83.7487, "America/Detroit", None)]
    conn.executemany("INSERT INTO bt_teams VALUES (?, ?, ?, ?, ?, ?, ?, ?)", teams)
    g = """INSERT INTO bt_games (id, season, week, season_type, start_date, start_tbd, home_team, away_team, home_points,
           away_points, neutral_site, venue_id, conference_game) VALUES (?, ?, ?, 'regular', ?, 0, ?, ?, ?, ?, 0, ?, ?)"""
    rows = [
        (1, 2025, 3, "2025-09-13T19:00:00Z", "Washington State", "Ohio State", 20, 31, 2, 0),   # last meeting: WSU lost
        (10, 2026, 5, "2026-10-03T23:00:00Z", "Washington State", "Colorado", 27, 24, 2, 0),  # WSU then a bye
        (11, 2026, 6, "2026-10-10T16:00:00Z", "Ohio State", "Colorado", 38, 10, 1, 0),        # OSU played last week
        (12, 2026, 7, "2026-10-17T16:00:00Z", "Ohio State", "Washington State", None, None, 1, 0),  # this game
        (13, 2026, 8, "2026-10-24T16:00:00Z", "Michigan", "Ohio State", None, None, None, 1),       # OSU next: rival
        (14, 2026, 7, "2026-10-17T20:00:00Z", "Colorado", "Michigan", None, None, 3, 0),
    ]
    conn.executemany(g, rows)
    conn.executemany("INSERT INTO bt_rankings VALUES (?, ?, ?, ?, ?, ?)",
                     [(2026, "regular", 7, "AP Top 25", "Ohio State", 4), (2026, "regular", 7, "AP Top 25", "Michigan", 11),
                      (2026, "regular", 9, "AP Top 25", "Washington State", 25)])   # a future poll must not leak
    conn.commit()
    return conn


def game(conn, gid):
    return dict(conn.execute("SELECT * FROM bt_games WHERE id=?", (gid,)).fetchone())


def test_travel_body_clock_rest_revenge_lookahead(hconn):
    import situational
    s = situational.game_situation(hconn, game(hconn, 12), as_of=datetime(2026, 10, 16, 17, tzinfo=timezone.utc))
    wsu, osu = s["teams"]["away"], s["teams"]["home"]
    assert 1700 <= wsu["travel_miles"] <= 1800 and osu["travel_miles"] == 0
    assert wsu["time_zones_crossed"] == 3 and wsu["body_clock_kickoff"] == "9:00 AM home time" and wsu["early_body_clock"]
    assert wsu["off_bye"] and wsu["rest_days"] == 13.7
    assert osu["rest_days"] == 7.0 and not osu["off_bye"] and not osu["short_week"]
    assert wsu["last_meeting"] == {"season": 2025, "score": "20-31", "won": False, "postseason": False}
    assert osu["next_game"]["opponent"] == "Michigan" and osu["next_game"]["rival"] == "The Game"
    assert osu["rank"] == 4 and wsu.get("rank") is None          # week-9 poll is not visible in week 7
    assert osu["record"] == "1-0" and wsu["record"] == "1-0"
    f = situational.flags(s)
    assert any("crosses 3 time zones (east)" in x for x in f)
    assert any("Revenge spot: Washington State" in x for x in f)
    assert any("Look-ahead risk: Ohio State plays Michigan (rival)" in x for x in f)
    assert any("coming off a bye" in x for x in f)
    assert "Washington State: record 1-0" in situational.describe(s)


def test_altitude_and_surface(hconn):
    import situational
    s = situational.game_situation(hconn, game(hconn, 14))
    assert s["elevation_ft"] == round(1655 * 3.28084) and s["surface"] == "grass"
    assert any("High altitude" in x for x in situational.flags(s))


def test_as_of_hides_later_results(hconn):
    import situational
    # Colorado's Oct 3 loss counts when the cutoff is after it, and is hidden when the cutoff is before it.
    after = situational.game_situation(hconn, game(hconn, 11), as_of=datetime(2026, 10, 9, tzinfo=timezone.utc))
    before = situational.game_situation(hconn, game(hconn, 11), as_of=datetime(2026, 10, 2, tzinfo=timezone.utc))
    assert after["teams"]["away"]["record"] == "0-1" and before["teams"]["away"]["record"] == "0-0"
