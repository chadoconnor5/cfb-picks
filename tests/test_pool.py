import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pool  # noqa: E402

HERE = os.path.dirname(__file__)
CARD = open(os.path.join(HERE, "data", "week7_card.txt")).read()
MATCHUPS = [("Ohio State", "Indiana"), ("Mississippi State", "LSU"), ("Alabama", "Tennessee"), ("Missouri", "Ole Miss"),
            ("North Carolina", "Duke"), ("Notre Dame", "BYU"), ("Wisconsin", "UCLA"), ("Michigan State", "Northwestern"),
            ("Texas Tech", "Arizona State"), ("Washington State", "Oregon State"), ("Western Michigan", "Central Michigan")]
WEEK = {"season": 2026, "week": 7, "season_type": "regular"}


def test_parse_week7_card():
    c = pool.parse_card(CARD)
    games = [g for g in c["games"] if g["kind"] == "game"]
    assert len(games) == 7 and c["games"][-1]["kind"] == "mid_major"
    osu = games[0]
    assert (osu["away"], osu["home"], osu["home_spread"], osu["total"], osu["weight"]) == ("Ohio State", "Indiana", 3.5, 50.5, 5)
    assert games[6]["total"] == 46.4                       # typo on the card is kept as written
    assert c["games"][-1]["total"] is None                 # mid-major is ATS only
    assert [p["line"] for p in c["props"]] == [150.5, 2.5, 700.5]
    assert [p["my_pick"] for p in c["props"]] == ["over", "under", "under"]
    assert c["tiebreaker"] == 347


def test_blank_card_without_picks():
    c = pool.parse_card("Ohio State @ Indiana (+3.5) o/u 50.5 *5 Point Game*\nMississippi State @ LSU (-11.5) o/u 58.5\n"
                        "Questions\n1. Will they rush for over or under 150.5 yards?\nCombined Score -")
    assert len(c["games"]) == 2 and c["games"][0]["my_ats_team"] is None
    assert c["props"][0]["my_pick"] is None and c["has_tiebreaker"] and c["tiebreaker"] is None


def test_grade_against_pool_line_and_flip():
    item = {"home_spread": 3.5, "total": 50.5, "my_ats": "away", "my_ou": "under", "flipped": False}
    game = {"home_points": 24, "away_points": 28, "completed": 1}   # Ohio State wins by 4 -> covers -3.5
    assert pool.grade_game_item(item, game) == ("W", "L")           # 52 points -> over
    flipped = {**item, "flipped": True}                             # card lists home/away reversed vs CFBD
    assert pool.grade_game_item(flipped, {"home_points": 28, "away_points": 24, "completed": 1}) == ("W", "L")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CFB_DB_PATH", str(tmp_path / "p.db"))
    monkeypatch.setenv("BACKTEST_DB_PATH", str(tmp_path / "h.db"))
    for m in ("app", "db", "history", "pool_api", "league_api"):
        sys.modules.pop(m, None)
    import app
    import db
    with db.tx() as conn:
        for i, (a, h) in enumerate(MATCHUPS, start=1):
            db.upsert_game(conn, {"id": i, **WEEK, "start_date": "2099-10-17T19:30:00Z", "home_team": h, "away_team": a,
                                  "home_conf": None, "away_conf": None, "neutral_site": 0, "venue": None, "venue_id": None,
                                  "home_points": None, "away_points": None, "completed": 0})
    import predict

    def fake_predict(game, notes="", context=None):
        return {"game_id": game["id"], "model": "m", "spread": game["spread"], "total": game["total"], "notes": "",
                "ats_pick": "home", "ou_pick": "under", "proj_home": 27, "proj_away": 20, "confidence": 60,
                "reasoning": "r", "key_factors": json.dumps(["k"]), "conditions_impact": "Not a factor.", "context": "{}"}
    monkeypatch.setattr(predict, "predict", fake_predict)
    monkeypatch.setattr(predict, "predict_prop", lambda prop, game, ctx: {"pick": "over", "projection": prop["line"] + 10,
                                                                         "confidence": 58, "reasoning": "r", "model": "m"})
    c = app.app.test_client()
    c.app_module = app
    return c


def test_pool_flow(client, monkeypatch):
    r = client.post("/api/pool/import", json={**WEEK, "text": CARD}).json
    assert r["games"] == 8 and r["props"] == 3 and r["unmatched"] == []

    card = client.get("/api/pool", query_string=WEEK).json
    items = card["items"]
    assert items[0]["my_ats"] == "away" and items[0]["my_ou"] == "under" and items[0]["data"]["weight"] == 5
    assert items[8]["data"]["team"] == "Northwestern" and items[8]["data"]["stat"] == "rush_yds"
    assert items[10]["data"]["scope"] == "combined"

    # Claude predicts against the POOL line
    for it in items:
        assert client.post(f"/api/pool/items/{it['id']}/predict").status_code == 200
    card = client.get("/api/pool", query_string=WEEK).json
    assert card["items"][0]["claude"]["pool_spread"] == 3.5 and card["items"][0]["claude"]["ats_pick"] == "home"
    assert card["items"][7]["claude"]["ou_pick"] is None          # mid-major has no total
    assert card["projected_combined"] == 7 * 47                   # mid-major excluded (league rule)

    # Change a pick and the tiebreaker; export matches the email format
    client.post(f"/api/pool/items/{items[1]['id']}/pick", json={"ats": "home", "ou": "over"})
    client.post(f"/api/pool/cards/{card['card']['id']}/tiebreaker", json={"value": 360})
    export = client.get("/api/pool", query_string=WEEK).json["export"].splitlines()
    assert export[0] == "Ohio State @ Indiana (+3.5) o/u 50.5 *5 Point Game*"
    assert export[1] == "Ohio State, U"
    assert export[3] == "LSU, O"
    assert "Questions" in export and "OVER" in export
    assert export[-3:] == ["Western Michigan @ Central Michigan (+6.5)", "WESTERN MICHIGAN", "Combined Score - 360"]

    # Grade: scores + box stats from CFBD (mocked)
    import cfbd
    finals = {1: (24, 28), 2: (35, 17), 3: (21, 24), 4: (31, 30), 5: (20, 17), 6: (17, 24), 7: (20, 17), 8: (24, 10),
              9: (30, 27), 10: (28, 31), 11: (21, 28)}
    def fake_games(season, week, season_type="regular"):
        return [{"id": i, **WEEK, "start_date": "2000-10-17T19:30:00Z", "home_team": h, "away_team": a,
                 "home_conf": None, "away_conf": None, "neutral_site": 0, "venue": None, "venue_id": None,
                 "home_points": finals[i][0], "away_points": finals[i][1], "completed": 1}
                for i, (a, h) in enumerate(MATCHUPS, start=1)]
    def team_box(gid, team, home, rush, total, sacks):
        return {"team": team, "homeAway": "home" if home else "away", "points": 0,
                "stats": [{"category": "rushingYards", "stat": str(rush)}, {"category": "totalYards", "stat": str(total)},
                          {"category": "sacks", "stat": str(sacks)}]}
    box = [{"id": 8, "teams": [team_box(8, "Northwestern", 1, 162, 380, 1), team_box(8, "Michigan State", 0, 80, 300, 2)]},
           {"id": 9, "teams": [team_box(9, "Arizona State", 1, 120, 400, 2), team_box(9, "Texas Tech", 0, 150, 420, 3)]},
           {"id": 10, "teams": [team_box(10, "Oregon State", 1, 140, 360, 2), team_box(10, "Washington State", 0, 100, 330, 1)]}]
    monkeypatch.setattr(cfbd, "fetch_games", fake_games)
    monkeypatch.setattr(cfbd, "_get", lambda path, **p: box)
    g = client.post(f"/api/pool/cards/{card['card']['id']}/grade").json
    items = {it["idx"]: it for it in g["items"]}
    # OSU@IND: OSU won 28-24, covers -3.5 (W, x5); 52 total over 50.5 -> my Under loses (L)
    assert (items[0]["ats_result"], items[0]["ou_result"]) == ("W", "L")
    assert items[8]["prop_actual"] == 162 and items[8]["prop_result"] == "W"     # rush over 150.5
    assert items[9]["prop_actual"] == 3 and items[9]["prop_result"] == "L"       # Texas Tech 3 sacks, I said under
    assert items[10]["prop_actual"] == 690 and items[10]["prop_result"] == "W"   # combined 690 < 700.5
    assert g["score"]["points"] >= 5                                            # the 5-point ATS win counts 5

    rec = client.get("/api/pool/record?season=2026").json
    assert rec["weeks"][0]["week"] == 7 and rec["totals"]["prop"]["W"] == 2


def test_default_deadline_is_friday_noon_local():
    from datetime import datetime
    import pool_api
    games = [{"start_date": "2026-10-17T16:00:00Z"}, {"start_date": "2026-10-17T23:30:00Z"},
             {"start_date": "2026-10-15T23:30:00Z"}]   # a Thursday game doesn't move the deadline
    d = datetime.fromisoformat(pool_api.default_deadline(games)).astimezone()
    assert (d.weekday(), d.hour, d.minute) == (4, 12, 0)
    assert pool_api.default_deadline([]) is None


def test_manual_card_lines_deadline_and_market(client):
    import db
    with db.tx() as conn:   # give game 1 a market line that differs from the pool's
        conn.execute("UPDATE games SET spread=4.5, total=51.5 WHERE id=1")
    assert client.post("/api/pool/cards", json=WEEK).status_code == 200
    card = client.get("/api/pool", query_string=WEEK).json
    cid = card["card"]["id"]
    assert card["card"]["deadline"] and not card["card"]["locked"] and card["items"] == []

    assert client.post(f"/api/pool/cards/{cid}/items", json={"kind": "game", "game_id": 1, "home_spread": "3.5",
                                                             "total": "50.5", "weight": 5}).status_code == 200
    assert client.post(f"/api/pool/cards/{cid}/items", json={"kind": "mid_major", "game_id": 11, "home_spread": 6.5}).status_code == 200
    assert client.post(f"/api/pool/cards/{cid}/items", json={
        "kind": "prop", "question": "Will Northwestern rush for over or under 150.5 yards against Michigan State?"}).status_code == 200
    assert client.post(f"/api/pool/cards/{cid}/items", json={"kind": "game", "game_id": 999, "home_spread": 1}).status_code == 400

    items = client.get("/api/pool", query_string=WEEK).json["items"]
    g, mid, prop = items
    assert g["data"]["weight"] == 5 and g["data"]["line_entered_at"]
    assert g["market"] == {"home_spread": 4.5, "total": 51.5}
    assert mid["data"]["total"] is None
    assert prop["data"]["line"] == 150.5 and prop["data"]["team"] == "Northwestern"

    # Claude predicts; then I correct the pool line -> prediction flagged stale
    client.post(f"/api/pool/items/{g['id']}/predict")
    assert client.get("/api/pool", query_string=WEEK).json["items"][0]["claude_stale"] is False
    assert client.post(f"/api/pool/items/{g['id']}/line", json={"home_spread": 3, "total": 49.5, "weight": 5}).status_code == 200
    g2 = client.get("/api/pool", query_string=WEEK).json["items"][0]
    assert g2["data"]["home_spread"] == 3 and g2["data"]["total"] == 49.5 and g2["claude_stale"] is True
    assert client.post(f"/api/pool/items/{prop['id']}/line", json={"line": 145.5}).status_code == 200

    # Deadline passes -> everything locks
    client.post(f"/api/pool/cards/{cid}/deadline", json={"deadline": "2000-01-01T12:00:00Z"})
    card = client.get("/api/pool", query_string=WEEK).json
    assert card["card"]["locked"] and all(it["locked"] for it in card["items"])
    for call in (lambda: client.post(f"/api/pool/items/{g['id']}/pick", json={"ats": "home"}),
                 lambda: client.post(f"/api/pool/items/{g['id']}/line", json={"home_spread": 1}),
                 lambda: client.delete(f"/api/pool/items/{g['id']}")):
        r = call()
        assert r.status_code == 400 and "locked" in r.json["error"]
    # ...but a missing pick can still be filled in on purpose (entering a past week)
    assert client.post(f"/api/pool/items/{g['id']}/pick", json={"ats": "home", "ou": "under", "fix": True}).status_code == 200
    g3 = client.get("/api/pool", query_string=WEEK).json["items"][0]
    assert g3["my_ats"] == "home" and g3["my_ou"] == "under"


def test_prompt_mentions_market_vs_pool():
    from predict import build_prompt
    game = {"id": 1, "season": 2026, "week": 7, "season_type": "regular", "start_date": "2099-10-17T19:30:00Z",
            "home_team": "Indiana", "away_team": "Ohio State", "home_conf": None, "away_conf": None, "neutral_site": 0,
            "venue": None, "spread": 3.5, "total": 50.5, "spread_open": None, "total_open": None}
    p = build_prompt(game, "", {"market_now": {"home": "Indiana", "spread": 4.5, "total": 51.5}})
    assert "Spread: Indiana +3.5" in p and "Market (current sportsbook line): Indiana +4.5, total 51.5" in p


def test_replay_after_deadline_is_strict(client, monkeypatch):
    """After the deadline, Claude can still predict (trial run) but only with cutoff-limited inputs."""
    import db
    import injuries
    import intel
    import predict
    import weather
    with db.tx() as conn:
        conn.execute("UPDATE games SET spread=5.5, total=52.5, spread_open=4.0, total_open=50.0, "
                     "weather=? WHERE id=1", ('{"wind_mph": 30}',))     # a post-cutoff forecast that must NOT be used
    client.post("/api/pool/cards", json=WEEK)
    cid = client.get("/api/pool", query_string=WEEK).json["card"]["id"]
    client.post(f"/api/pool/cards/{cid}/items", json={"kind": "game", "game_id": 1, "home_spread": 3.5, "total": 50.5})
    client.post(f"/api/pool/cards/{cid}/deadline", json={"deadline": "2026-10-16T17:00:00Z"})
    seen = {}
    monkeypatch.setattr(injuries, "fetch_report", lambda game, cutoff=None: seen.setdefault("inj_cutoff", cutoff) and
                        {"report": "OSU RB out (Oct 14)", "sources": []})
    monkeypatch.setattr(intel, "fetch", lambda team, season, cutoff=None: seen.setdefault(f"intel_{team}", cutoff) and
                        {"staff": [], "hot_seat": {"status": "none"}, "rumors": []})
    monkeypatch.setattr(weather, "forecast_as_of", lambda *a: None)
    orig = predict.predict
    def capture(game, notes="", context=None):
        seen["context"], seen["game"] = context, game
        return orig(game, notes, context)
    monkeypatch.setattr(predict, "predict", capture)

    it = client.get("/api/pool", query_string=WEEK).json["items"][0]
    r = client.post(f"/api/pool/items/{it['id']}/predict").json
    assert r["replay"] is True and r["as_of"].startswith("2026-10-16T17:00")
    assert seen["inj_cutoff"].isoformat().startswith("2026-10-16T17:00")
    assert seen["intel_Indiana"] and seen["intel_Ohio State"]
    ctx = seen["context"]
    assert ctx["replay"] and ctx["market_now"]["spread"] == 4.0 and "opening" in ctx["market_now"]["label"]
    assert ctx["assessment"]["weather"] is None                       # the stored (later) forecast was ignored
    assert ctx["injuries"]["report"].startswith("OSU RB out")
    assert any("cutoff" in a for a in ctx["audit"])
    assert seen["game"]["spread"] == 3.5                              # predicted against the POOL line


def test_parse_card_short_names_and_answers_on_the_question_line():
    text = ("Colorado @ Georgia Tech (-6.5) o/u 50.5 Thursday\nGT,  O\n"
            "Questions\n2. Will the teams combine for over or under 2.5 turnovers? OVER\n\n"
            "3. Will their team combine for over or under 150.5 rushing yards against Wisconsin? under\n")
    parsed = pool.parse_card(text)
    games = pool.match_games(parsed["games"], [{"id": 1, "home_team": "Georgia Tech", "away_team": "Colorado"}])
    assert games[0]["my_ats"] == "home" and games[0]["my_ou"] == "over"
    assert [(p["n"], p["line"], p["my_pick"]) for p in parsed["props"]] == [(2, 2.5, "over"), (3, 150.5, "under")]
    assert parsed["props"][0]["question"].endswith("turnovers?")


def test_which_questions_and_tiebreak_on_next_line():
    text = ("Ohio State @ Texas (-1.5) o/u 48.5 *5 Point Game*\nBUCKEYES, U\n\nQuestions\n"
            "2. Wake Forest and Purdue face off. Which QB will end the game with more passing yards? (Lopez or Browne)\nBROWNE\n"
            "3. Mississippi State and Minnesota are both in the bottom half. Which team will have more rushing yards?\nMINNESOTA\n\n"
            "Combined Score - \n318\n")
    parsed = pool.parse_card(text)
    assert parsed["tiebreaker"] == 318
    wg = [{"id": 1, "home_team": "Texas", "away_team": "Ohio State"}, {"id": 2, "home_team": "Purdue", "away_team": "Wake Forest"},
          {"id": 3, "home_team": "Minnesota", "away_team": "Mississippi State"}]
    assert pool.match_games(parsed["games"], wg)[0]["my_ats"] == "away"
    q2, q3 = [pool.interpret_prop(p, wg) for p in parsed["props"]]
    assert (q2["scope"], q2["choices"], q2["my_pick"]) == ("choice", ["Lopez", "Browne"], "Browne")
    assert (q3["scope"], q3["stat"], q3["my_pick"]) == ("compare", "rush_yds", "Minnesota")
    assert set(q3["choices"]) == {"Minnesota", "Mississippi State"}
    box = {(3, "Minnesota"): {"rush_yds": 180}, (3, "Mississippi State"): {"rush_yds": 120}}
    assert pool.prop_actual(q3, box) == "Minnesota" and pool.grade_prop("Minnesota", None, "Minnesota") == "W"
    assert pool.prop_actual(q2, box) is None and pool.grade_prop("Lopez", None, "Browne") == "L"


def test_prop_prefers_the_longer_team_name():
    wg = [{"id": 1, "home_team": "Texas", "away_team": "Florida"}, {"id": 2, "home_team": "Texas Tech", "away_team": "Arizona State"},
          {"id": 3, "home_team": "Purdue", "away_team": "Washington"}, {"id": 4, "home_team": "Oregon State", "away_team": "Washington State"}]
    q = pool.interpret_prop({"kind": "prop", "n": 1, "question": "Texas Tech is undefeated. Will they register over or under 2.5 sacks?",
                             "line": 2.5, "unit": "", "my_pick": None}, wg)
    assert (q["team"], q["game_id"]) == ("Texas Tech", 2)
    q = pool.interpret_prop({"kind": "prop", "n": 2, "question": "Washington State travels to Oregon State. Will the teams combine for "
                             "over or under 700.5 total yards?", "line": 700.5, "unit": "", "my_pick": None}, wg)
    assert (q["team"], q["game_id"]) == ("Washington State", 4)
