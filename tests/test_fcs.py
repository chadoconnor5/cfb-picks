import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import fcs  # noqa: E402

BOARD = {"games": [
    {"game": {"gameState": "final", "startDate": "09-06-2025",
              "home": {"score": "38", "names": {"short": "North Dakota St.", "seo": "north-dakota-st"}, "conferences": [{"conferenceDivision": "FCS"}]},
              "away": {"score": "10", "names": {"short": "Montana St.", "seo": "montana-st"}, "conferences": [{"conferenceDivision": "FCS"}]}}},
    {"game": {"gameState": "final", "startDate": "09-13-2025",
              "home": {"score": "24", "names": {"short": "Colorado", "seo": "colorado"}, "conferences": [{"conferenceDivision": "FBS"}]},
              "away": {"score": "27", "names": {"short": "North Dakota St.", "seo": "north-dakota-st"}, "conferences": [{"conferenceDivision": "FCS"}]}}},
    {"game": {"gameState": "pre", "startDate": "09-20-2025",
              "home": {"score": "", "names": {"short": "North Dakota St."}}, "away": {"score": "", "names": {"short": "UND"}}}},
    {"game": {"gameState": "final", "startDate": "09-06-2025",
              "home": {"score": "20", "names": {"short": "Sacramento St.", "seo": "sacramento-st"}},
              "away": {"score": "21", "names": {"short": "UC Davis", "seo": "uc-davis"}}}},
]}


def test_parse_and_summarize():
    games = fcs.parse_scoreboard(BOARD, "North Dakota State")
    assert len(games) == 2
    s = fcs.summarize(games)
    assert s["record"] == "2-0" and s["ppg"] == 32.5 and s["vs_fbs"] == ["W 27-24 vs Colorado"]
    sac = fcs.summarize(fcs.parse_scoreboard(BOARD, "Sacramento State"))
    assert sac["record"] == "0-1"


def test_history_uses_cache_and_describe(monkeypatch):
    calls = []
    monkeypatch.setattr(fcs, "_get", lambda path: calls.append(path) or (BOARD if "/01/" in path else {"games": []}))
    monkeypatch.setattr(fcs, "team_stats", lambda team, season, division="fcs": [{"stat": "Scoring Offense", "rank": 3}])
    h = fcs.history("North Dakota State", 2026, years=1)
    assert h[2025]["record"] == "2-0" and any("/scoreboard/football/fcs/2025/01/all-conf" == c for c in calls)
    text = fcs.describe("North Dakota State", h)
    assert "2025 (FCS): 2-0" in text and "vs FBS: W 27-24 vs Colorado" in text and "Scoring Offense: FCS rank 3" in text
