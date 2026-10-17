import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.dirname(__file__))

import league  # noqa: E402
from test_pool import CARD, MATCHUPS, WEEK, client  # noqa: E402,F401


def item(idx, kind, home_spread=0, total=None, weight=1, hp=None, ap=None, line=None, actual=None, n=None):
    if kind == "prop":
        return {"idx": idx, "kind": "prop", "data": {"line": line, "n": n}, "prop_actual": actual, "game": None}
    return {"idx": idx, "kind": kind, "data": {"away": f"A{idx}", "home": f"H{idx}", "home_spread": home_spread,
                                               "total": total, "weight": weight, "flipped": False},
            "game": {"completed": hp is not None, "home_points": hp, "away_points": ap}}


ITEMS = [
    item(0, "game", 3.5, 50.5, weight=5, hp=24, ap=28),   # away covers (+5 ATS), total 52 over
    item(1, "game", -11.5, 58.5, hp=35, ap=17),           # home covers, total 52 under
    item(2, "mid_major", 6.5, None, hp=21, ap=28),        # away covers
    item(3, "prop", line=150.5, actual=162, n=1),         # over
]


def test_score_entry_default_rules():
    cfg = league.merged({})
    perfect = {"0": {"ats": "away", "ou": "over"}, "1": {"ats": "home", "ou": "under"}, "2": {"ats": "away"}, "3": {"pick": "over"}}
    s = league.score_entry(ITEMS, perfect, cfg)
    b = s["breakdown"]
    assert b["bonus_game"] == 5 + 1 and b["ats"] == 3 and b["ou"] == 1 and b["mid_major"] == 2 and b["prop"] == 1
    assert s["base"] == 13 and b["correct"] == 6 and b["graded"] == 6
    both = league.score_entry(ITEMS, perfect, league.merged({"bonus_game_mode": "both"}))
    assert both["breakdown"]["bonus_game"] == 10


def test_actual_combined_excludes_mid_major():
    assert league.actual_combined(ITEMS) == 52 + 52
    assert league.actual_combined([item(0, "game", 1, 40)]) is None   # not final yet


def test_week_potw_and_tiebreak():
    entries = [
        {"player": "Ann", "picks": {"0": {"ats": "away", "ou": "over"}, "1": {"ats": "home"}}, "tiebreaker": 100},
        {"player": "Bob", "picks": {"0": {"ats": "away", "ou": "over"}, "1": {"ats": "home"}}, "tiebreaker": 110},
        {"player": "Cy", "picks": {"1": {"ats": "away", "ou": "under"}}, "tiebreaker": 104},
    ]
    w = league.score_week(ITEMS, entries, {})
    rows = {r["player"]: r for r in w["rows"]}
    assert w["actual_combined"] == 104 and w["complete"]
    assert rows["Ann"]["potw"] and rows["Bob"]["potw"] and rows["Ann"]["total"] == 9 + 3     # tie: both get +3
    w2 = league.score_week(ITEMS, entries, {"potw_ties": "closest_combined", "tiebreak": {"type": "closest", "points": 2}})
    rows = {r["player"]: r for r in w2["rows"]}
    assert rows["Cy"]["tiebreak_points"] == 2                     # exact guess
    assert rows["Ann"]["potw"] and not rows["Bob"]["potw"]        # Ann's guess (100) closer than Bob's (110)


def test_tiebreak_rules():
    g = {"a": 100, "b": 103, "c": 103, "d": 130}
    assert league.tiebreak_points({"type": "closest", "points": 1}, g, 104) == {"a": 0, "b": 1, "c": 1, "d": 0}
    assert league.tiebreak_points({"type": "tiers", "points": [3, 2]}, g, 104) == {"a": 2, "b": 3, "c": 3, "d": 0}
    assert league.tiebreak_points({"type": "within", "points": 1, "margin": 5}, g, 104) == {"a": 1, "b": 1, "c": 1, "d": 0}
    assert league.tiebreak_points({"type": "exact", "points": 5}, g, 103)["b"] == 5


def test_infer_recovers_hidden_rules():
    rnd = random.Random(1)
    secret = league.merged({"tiebreak": {"type": "closest", "points": 2}, "potw_ties": "closest_combined", "bonus_game_mode": "ats"})
    weeks = []
    for wk in range(6):
        items = [item(0, "game", 3.5, 50.5, weight=5, hp=rnd.randint(10, 40), ap=rnd.randint(10, 40))] + \
                [item(i, "game", rnd.choice([-7.5, 3.5, -2.5]), 47.5, hp=rnd.randint(10, 40), ap=rnd.randint(10, 40)) for i in range(1, 7)] + \
                [item(7, "mid_major", 6.5, None, hp=rnd.randint(10, 40), ap=rnd.randint(10, 40)),
                 item(8, "prop", line=150.5, actual=rnd.randint(100, 200), n=1)]
        entries = []
        for p in range(8):
            picks = {str(i): {"ats": rnd.choice(["home", "away"]), "ou": rnd.choice(["over", "under"])} for i in range(7)}
            picks["7"] = {"ats": rnd.choice(["home", "away"])}
            picks["8"] = {"pick": rnd.choice(["over", "under"])}
            entries.append({"player": f"P{p}", "picks": picks, "tiebreaker": rnd.randint(280, 420)})
        truth = {r["player"]: r["total"] for r in league.score_week(items, entries, secret)["rows"]}
        for e in entries:
            e["reported_points"] = truth[e["player"]]
        weeks.append({"week": wk + 1, "items": items, "entries": entries})
    res = league.infer_rules(weeks, league.merged({}))
    best = res["candidates"][0]
    assert best["match_pct"] == 100.0
    assert best["config"]["tiebreak"] == {"type": "closest", "points": 2}
    assert "closest combined-score guess gets 2" in best["rule"]


def test_league_api_flow(client, monkeypatch):
    import cfbd
    client.post("/api/pool/import", json={**WEEK, "text": CARD})
    card = client.get("/api/pool", query_string=WEEK).json
    for it in card["items"]:
        client.post(f"/api/pool/items/{it['id']}/predict")
    # Three members: me (the real reply), and two friends with different picks
    friend = CARD.replace("Ohio State, U", "INDIANA, O").replace("MISS ST, U", "LSU, O").replace("Combined Score - 347", "Combined Score - 360")
    lazy = "Ohio State @ Indiana (+3.5) o/u 50.5\nIndiana, U\nCombined Score - 300"
    assert client.post("/api/league/entries", json={**WEEK, "name": "Chad", "text": CARD, "reported_points": 30}).json["warnings"] == []
    r = client.post("/api/league/entries", json={**WEEK, "name": "Pat", "text": friend}).json
    assert r["tiebreaker"] == 360
    r = client.post("/api/league/entries", json={**WEEK, "name": "Lee", "text": lazy}).json
    assert any("No pick for" in w for w in r["warnings"])

    finals = {1: (24, 28), 2: (35, 17), 3: (21, 24), 4: (31, 30), 5: (20, 17), 6: (17, 24), 7: (20, 17), 8: (24, 10),
              9: (30, 27), 10: (28, 31), 11: (21, 28)}
    monkeypatch.setattr(cfbd, "fetch_games", lambda season, week, season_type="regular": [
        {"id": i, **WEEK, "start_date": "2000-10-17T19:30:00Z", "home_team": h, "away_team": a, "home_conf": None,
         "away_conf": None, "neutral_site": 0, "venue": None, "venue_id": None, "home_points": finals[i][0],
         "away_points": finals[i][1], "completed": 1} for i, (a, h) in enumerate(MATCHUPS, start=1)])
    box = [{"id": 8, "teams": [{"team": "Northwestern", "homeAway": "home", "stats": [{"category": "rushingYards", "stat": "162"}]},
                               {"team": "Michigan State", "homeAway": "away", "stats": []}]},
           {"id": 9, "teams": [{"team": "Texas Tech", "homeAway": "away", "stats": [{"category": "sacks", "stat": "3"}]},
                               {"team": "Arizona State", "homeAway": "home", "stats": []}]},
           {"id": 10, "teams": [{"team": "Oregon State", "homeAway": "home", "stats": [{"category": "totalYards", "stat": "360"}]},
                                {"team": "Washington State", "homeAway": "away", "stats": [{"category": "totalYards", "stat": "330"}]}]}]
    monkeypatch.setattr(cfbd, "_get", lambda path, **p: box)
    client.post(f"/api/pool/cards/{card['card']['id']}/grade")

    w = client.get("/api/league/week", query_string=WEEK).json
    rows = {r["player"]: r for r in w["rows"]}
    actual = sum(sum(finals[i]) for i in range(1, 8))
    assert w["actual_combined"] == actual and w["complete"]
    chad = rows["Chad"]
    # Chad: OSU covers -3.5 (5 pts, 5-pt game); 52 > 50.5 so U loses; props: over 150.5 W, under 2.5 sacks L, under 700.5 W
    assert chad["breakdown"]["bonus_game"] == 5 and chad["breakdown"]["prop"] == 2
    assert chad["distance"] == abs(347 - actual)
    assert sum(r["potw"] for r in w["rows"]) >= 1
    assert w["claude"]["player"] == "Claude (not in league)" and not w["claude"]["potw"]

    lb = client.get("/api/league/leaderboard?season=2026").json
    assert [s["player"] for s in lb["standings"]][0] in ("Chad", "Pat") and lb["standings"][0]["rank"] == 1
    assert lb["weeks"][0]["potw"]

    # Manual question result for something automatic grading can't read
    q = [it for it in client.get("/api/pool", query_string=WEEK).json["items"] if it["kind"] == "prop"][1]
    client.post(f"/api/pool/items/{q['id']}/actual", json={"value": 2})
    assert {it["idx"]: it for it in client.get("/api/pool", query_string=WEEK).json["items"]}[q["idx"]]["prop_actual"] == 2

    inf = client.get("/api/league/infer?season=2026").json
    assert inf["candidates"] and inf["residuals"][0]["player"] == "Chad"

    # Scoring is editable
    client.post("/api/league/scoring", json={"ats": 3, "ou": 1, "prop": 1, "mid_major": 2, "bonus_game_mode": "both",
                                             "tiebreak": {"type": "closest", "points": 1}, "potw_bonus": 3, "potw_ties": "all"})
    assert client.get("/api/league/scoring").json["bonus_game_mode"] == "both"


def test_short_team_names():
    import pool
    cases = {("UNC", ("North Carolina", "Duke")): "North Carolina", ("MISS ST", ("Mississippi State", "LSU")): "Mississippi State",
             ("TENN", ("Alabama", "Tennessee")): "Tennessee", ("WESTERN MICH", ("Western Michigan", "Central Michigan")): "Western Michigan",
             ("CMU", ("Western Michigan", "Central Michigan")): "Central Michigan", ("Bama", ("Alabama", "Tennessee")): "Alabama",
             ("Wazzu", ("Washington State", "Oregon State")): "Washington State", ("Mizzou", ("Missouri", "Ole Miss")): "Missouri",
             ("ND", ("Notre Dame", "BYU")): "Notre Dame", ("Xyz", ("Notre Dame", "BYU")): None}
    for (short, teams), want in cases.items():
        assert pool.which_team(short, list(teams)) == want, short


def test_highlights_and_names():
    hl = league.DEFAULT_HIGHLIGHTS
    assert league.highlight_for("Ron O’Connor", hl) == "gold"      # curly apostrophe
    assert league.highlight_for("ron o'connor", hl) == "gold"
    for n in ("Patrick Kenney", "Zach Kenney", "Kevin Kenney", "Darren Kenney"):
        assert league.highlight_for(n, hl) == "red"
    assert league.highlight_for("Chad O'Connor", hl) is None


def test_sheets_and_csv(client, monkeypatch):
    import cfbd
    client.post("/api/pool/import", json={**WEEK, "text": CARD})
    client.post("/api/league/entries", json={**WEEK, "name": "Ron O’Connor", "text": CARD})
    client.post("/api/league/entries", json={**WEEK, "name": "Patrick Kenney",
                                             "text": CARD.replace("Ohio State, U", "INDIANA, O").replace("Combined Score - 347", "Combined Score - 352")})
    finals = {i: (24, 28) for i in range(1, 12)}
    monkeypatch.setattr(cfbd, "fetch_games", lambda season, week, season_type="regular": [
        {"id": i, **WEEK, "start_date": "2000-10-17T19:30:00Z", "home_team": h, "away_team": a, "home_conf": None,
         "away_conf": None, "neutral_site": 0, "venue": None, "venue_id": None, "home_points": finals[i][0],
         "away_points": finals[i][1], "completed": 1} for i, (a, h) in enumerate(MATCHUPS, start=1)])
    monkeypatch.setattr(cfbd, "_get", lambda path, **p: [])
    card = client.get("/api/pool", query_string=WEEK).json
    client.post(f"/api/pool/cards/{card['card']['id']}/grade")

    sh = client.get("/api/league/sheet/week", query_string=WEEK).json
    rows = {r["player"]: r for r in sh["rows"]}
    assert rows["Ron O’Connor"]["highlight"] == "gold" and rows["Patrick Kenney"]["highlight"] == "red"
    assert len(sh["columns"]) == 11 and rows["Ron O’Connor"]["cells"][0]["label"] == "Ohio State, U"
    assert rows["Ron O’Connor"]["cells"][0]["ats_result"] == "W"           # OSU won 28-24, covers -3.5
    assert rows["Patrick Kenney"]["cells"][0]["label"] == "Indiana, O"

    season = client.get("/api/league/sheet/season?season=2026").json
    assert season["weeks"] == [7] and season["rows"][0]["behind"] == 0
    assert {r["player"]: r["highlight"] for r in season["rows"]}["Patrick Kenney"] == "red"

    csv_w = client.get("/api/league/sheet/week", query_string={**WEEK, "format": "csv"})
    assert csv_w.mimetype == "text/csv" and "attachment" in csv_w.headers["Content-Disposition"]
    import csv
    import io
    table = list(csv.reader(io.StringIO(csv_w.get_data(as_text=True))))
    assert table[0][:3] == ["Rank", "Player", "Ohio State @ Indiana (Indiana +3.5, o/u 50.5) [5 pts]"]
    ron = next(r for r in table[1:] if r[1] == "Ron O\u2019Connor")
    assert ron[2].startswith("Ohio State, U (W")
    csv_s = client.get("/api/league/sheet/season?season=2026&format=csv").get_data(as_text=True)
    assert csv_s.splitlines()[0] == "Rank,Player,Week 7,Total,Behind,POTW,Pick %"

    client.post("/api/league/highlights", json={"Chad O'Connor": "blue", "Bad": "purple"})
    assert client.get("/api/league/highlights").json == {"Chad O'Connor": "blue"}
