import os
import sys
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

TEAMS = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel", "India", "Juliet", "Kilo", "Lima"]


@pytest.fixture
def hconn(tmp_path):
    from backtest import collect
    conn = collect.connect(str(tmp_path / "h.db"))
    gid = 0
    for season in (2022, 2023, 2024, 2025):
        for wk in range(1, 7):
            day = date(season, 9, 1) + timedelta(days=7 * (wk - 1) + (14 if wk == 4 else 0))   # week 4 after a bye
            for i in range(0, len(TEAMS), 2):
                h, a = TEAMS[(i + wk) % 12], TEAMS[(i + wk + 5) % 12]
                if h == a:
                    continue
                gid += 1
                alpha_home, alpha_away = h == "Alpha", a == "Alpha"
                # Alpha wins and covers every game from 2023 on (new coach); loses in 2022
                if alpha_home or alpha_away:
                    good = season >= 2023
                    hp, ap = (31, 10) if (alpha_home == good) else (10, 31)
                else:
                    hp, ap = 24, 20
                conn.execute("""INSERT INTO bt_games (id, season, week, season_type, start_date, start_tbd, home_team, away_team,
                                home_points, away_points, neutral_site, venue_id, spread_close, total_close, conference_game)
                                VALUES (?, ?, ?, 'regular', ?, 0, ?, ?, ?, ?, 0, NULL, -3, 45, 1)""",
                             (gid, season, wk, f"{day.isoformat()}T19:00:00Z", h, a, hp, ap))
                for team, opp, pts in ((h, a, hp), (a, h, ap)):
                    off = 0.35 if (team == "Alpha" and season >= 2024) else 0.05 + 0.01 * TEAMS.index(team)
                    conn.execute("""INSERT INTO bt_team_games (game_id, team, opponent, points, off_ppa, pass_ppa, rush_ppa,
                                    pass_expl, def_ppa, def_pass_ppa, def_rush_ppa, sacks) VALUES (?, ?, ?, ?, ?, ?, ?, 1.2, 0.1, 0.1, 0.1, 2)""",
                                 (gid, team, opp, pts, off, off, off))
        for i, t in enumerate(TEAMS):
            talent = 900 - 10 * i
            sp = (talent - 850) / 5 + (12 if t == "Alpha" and season >= 2023 else 0)   # Alpha over-achieves under Coach A
            conn.execute("INSERT INTO bt_talent VALUES (?, ?, ?)", (season, t, talent))
            conn.execute("INSERT INTO bt_sp VALUES (?, ?, ?, ?, ?, ?, ?)", (season, t, sp, i + 1, 30, 20, 0.5))
            coach = "Coach A" if (t == "Alpha" and season >= 2023) else f"Coach {t}"
            conn.execute("INSERT INTO bt_coaches VALUES (?, ?, ?, NULL, 6, 3, 3, 0, NULL, NULL, NULL, NULL)", (season, t, coach))
        conn.execute("INSERT INTO bt_rankings VALUES (?, 'regular', 1, 'AP Top 25', 'Foxtrot', 8)", (season,))
    # 2025: Bravo had an interim coach too -> that season is skipped for Coach Bravo's game splits
    conn.execute("INSERT INTO bt_coaches VALUES (2025, 'Bravo', 'Interim Guy', NULL, 2, 1, 1, 0, NULL, NULL, NULL, NULL)")
    conn.commit()
    return conn


def test_head_coach_profile(hconn):
    import coaches
    p = coaches.profile(hconn, "Alpha", 2026)
    assert p["coach"] == "Coach A" and p["tenure"]["seasons_here"] == 3 and p["tenure"]["first_season_here"] == 2023
    s = p["splits"]
    assert s["overall"]["su"]["l"] == 0 and s["overall"]["n"] > 10
    assert s["vs_ranked"]["n"] > 0 and s["vs_ranked"]["su"]["w"] == s["vs_ranked"]["n"]
    assert s["after_bye"]["n"] > 0 and s["conference"]["n"] == s["overall"]["n"]
    assert p["talent_score"]["avg_residual"] > 8      # does more with less
    assert p["talent"]["rank"] == 1
    text = coaches.describe(p)
    assert "HC Coach A" in text and "does more with less" in text


def test_interim_season_skipped(hconn):
    import coaches
    seasons = {g["season"] for g in coaches.coach_games(hconn, "Coach Bravo")}
    assert 2025 not in seasons and 2024 in seasons


def test_staff_grading_first_year_jump(hconn):
    import coaches
    member = {"name": "Ace OC", "role": "Offensive Coordinator", "play_caller": True,
              "career": [{"team": "Alpha", "role": "Offensive Coordinator", "start": 2024, "end": None}]}
    stops = coaches.grade_staff_member(hconn, member, max_season=2025)
    assert stops and stops[0]["avg"]["offense EPA/play"] >= 90
    assert stops[0]["first_year_change"] > 30          # unit jumped when he arrived
    text = coaches.describe_staff([{**member, "graded": stops}])
    assert "Ace OC (play-caller)" in text and "year-1 change" in text
    assert coaches.role_units("Offensive Line Coach")[1][1] == "sacks_allowed"
    assert coaches.role_units("Special Teams Coordinator")[0][1] == "st"


def test_asof_profiles_and_history(hconn):
    from datetime import datetime, timezone
    import analytics
    import history
    early = datetime(2025, 9, 10, tzinfo=timezone.utc)     # after week 2 of 2025 only
    late = datetime(2025, 12, 1, tzinfo=timezone.utc)
    p_early, p_late = analytics.asof_profiles(hconn, 2025, early), analytics.asof_profiles(hconn, 2025, late)
    assert p_early["Alpha"]["games"] < p_late["Alpha"]["games"]
    assert len(history.team_games(hconn, "Alpha", [2025], as_of=early)) < len(history.team_games(hconn, "Alpha", [2025]))
    assert all(r["date"] < "2025-09-10" for r in history.team_games(hconn, "Alpha", [2025], as_of=early))
