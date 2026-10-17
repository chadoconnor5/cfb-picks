import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

from test_pool import CARD, MATCHUPS, WEEK, client  # noqa: E402,F401  (client is a pytest fixture)

import review  # noqa: E402


def rows_fixture():
    base = {"pool_spread": 3.5, "pool_total": 50.5, "weight": 1}
    return [
        {**base, "idx": 0, "type": "ats", "pick": "home", "confidence": 62, "result": "W", "mine": "L", "my_pick": "away",
         "market": {"home_spread": 2.5, "total": 49.5}},   # home got 3.5 in pool, closed 2.5 -> beat the close by 1
        {**base, "idx": 0, "type": "ou", "pick": "under", "confidence": 57, "result": "L", "mine": "L", "my_pick": "under",
         "market": {"home_spread": 2.5, "total": 49.5}},   # total closed lower -> beat the close on the under
        {**base, "idx": 1, "type": "ats", "pick": "away", "confidence": 68, "result": "L", "mine": "W", "my_pick": "home",
         "market": {"home_spread": 1.5, "total": 50.5}},   # market moved toward home -> picked against the move
        {"idx": 9, "type": "prop", "pick": "over", "confidence": 55, "result": "W", "mine": "W"},
    ]


def test_stats_calibration_clv_agreement():
    s = review.stats(rows_fixture())
    assert s["overall"] == {"w": 2, "l": 2, "p": 0, "pct": 50.0}
    assert s["by_type"]["ats"]["w"] == 1 and s["by_type"]["prop"]["w"] == 1
    assert {b["range"] for b in s["calibration"]} == {"55-59", "60-64", "65-69"}
    clv = s["closing_line_value"]
    assert clv["beat_close_pct"] == round(100 * 2 / 3, 1)
    assert clv["with_market_move"] == {"w": 1, "l": 1, "p": 0, "pct": 50.0}
    assert clv["against_market_move"]["l"] == 1
    assert s["agreement"]["n"] == 1 and s["agreement"]["me_when_disagree"]["w"] == 1


def test_learning_loop(client, monkeypatch):
    import cfbd
    import db
    # Week 7: import, predict, grade
    client.post("/api/pool/import", json={**WEEK, "text": CARD})
    card = client.get("/api/pool", query_string=WEEK).json
    for it in card["items"]:
        client.post(f"/api/pool/items/{it['id']}/predict")
    finals = {i: (24, 28) for i in range(1, 12)}
    monkeypatch.setattr(cfbd, "fetch_games", lambda season, week, season_type="regular": [
        {"id": i, **WEEK, "start_date": "2000-10-17T19:30:00Z", "home_team": h, "away_team": a, "home_conf": None,
         "away_conf": None, "neutral_site": 0, "venue": None, "venue_id": None, "home_points": finals[i][0],
         "away_points": finals[i][1], "completed": 1} for i, (a, h) in enumerate(MATCHUPS, start=1)])
    monkeypatch.setattr(cfbd, "_get", lambda path, **p: [{"id": 1, "teams": [
        {"team": "Indiana", "homeAway": "home", "points": 24, "stats": [{"category": "rushingYards", "stat": "90"}]},
        {"team": "Ohio State", "homeAway": "away", "points": 28, "stats": [{"category": "rushingYards", "stat": "210"}]}]}])
    client.post(f"/api/pool/cards/{card['card']['id']}/grade")
    graded = client.get("/api/pool", query_string=WEEK).json["items"][0]
    assert graded["data"]["actual"]["away"]["rush_yds"] == 210        # box score saved for the review

    seen = {}
    def fake_review(rows, week_stats, season, week, prior):
        seen["rows"], seen["prior"] = rows, prior
        return {"items": [{"idx": r["idx"], "type": r["type"], "verdict": "wrong_read", "miss_category": "matchup_style",
                           "what_happened": "x", "lesson": "y"} for r in rows],
                "summary": "Too many unders in shootouts.",
                "lessons": [{"text": "Don't take unders when both offenses rank top-20 in EPA.", "applies_to": "ou",
                             "evidence": "3 unders lost"}],
                "calibration_note": "Overconfident at 60+."}
    monkeypatch.setattr(review, "claude_review", fake_review)
    r = client.post(f"/api/pool/cards/{card['card']['id']}/review").json
    assert r["summary"].startswith("Too many") and r["stats"]["n"] > 0
    assert any(row["type"] == "ou" for row in seen["rows"]) and seen["prior"] == []

    # Week 8 sees week 7's lesson + calibration; a week-6 replay must not.
    import pool_api
    with db.tx() as conn:
        w8 = pool_api.lessons_for(conn, 2026, 8)
        w6 = pool_api.lessons_for(conn, 2026, 6)
    assert any("Don't take unders" in x for x in w8)
    assert not any("Don't take unders" in x for x in w6)

    season = client.get("/api/review?season=2026").json
    assert season["weeks"][0]["review"]["summary"].startswith("Too many")
    lesson_id = season["lessons"][0]["id"]
    client.post(f"/api/lessons/{lesson_id}", json={"active": False})
    with db.tx() as conn:
        assert not any("Don't take unders" in x for x in pool_api.lessons_for(conn, 2026, 8))

    # Lessons reach the prompt
    from predict import build_prompt
    game = {"id": 1, "season": 2026, "week": 8, "season_type": "regular", "start_date": "2099-10-24T19:30:00Z",
            "home_team": "Indiana", "away_team": "Ohio State", "home_conf": None, "away_conf": None, "neutral_site": 0,
            "venue": None, "spread": 3.5, "total": 50.5, "spread_open": None, "total_open": None}
    p = build_prompt(game, "", {"lessons": ["[week 7, ou] Don't take unders when both offenses rank top-20 in EPA."]})
    assert "Lessons from grading your own past picks" in p and "Don't take unders" in p
