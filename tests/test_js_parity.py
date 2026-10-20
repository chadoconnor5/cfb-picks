"""The website runs the pick logic in JavaScript (docs/logic.js). These tests feed the same inputs to the
Python modules and to the JavaScript and require identical answers."""
import copy
import difflib
import json
import os
import random
import shutil
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(__file__))

import grading  # noqa: E402
import league  # noqa: E402
import manual  # noqa: E402
import pool  # noqa: E402
import predict  # noqa: E402
import review  # noqa: E402
import teams as teams_mod  # noqa: E402
from fake_services import GAMES, WEEK7  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

CARD = open(os.path.join(ROOT, "tests", "data", "week7_card.txt")).read()
REF = teams_mod.load_reference(2026)
NICK = {k: v for k, v in json.load(open(os.path.join(ROOT, "reference", "team_nicknames.json"))).items() if not k.startswith("_")}


def js(calls):
    """calls: [(fn, args)] -> list of values (raises on a JS error)."""
    p = subprocess.run(["node", os.path.join(ROOT, "tests", "js", "runner.js")], input=json.dumps(
        [{"fn": f, "args": a} for f, a in calls]), capture_output=True, text=True, check=True)
    out = json.loads(p.stdout)
    return out


def same(py, js_value):
    return json.loads(json.dumps(py, default=list)) == js_value


def week_games(week):
    out = []
    for g in GAMES:
        if g["week"] == week:
            out.append({"id": g["id"], "home_team": g["homeTeam"], "away_team": g["awayTeam"]})
    return out


def test_difflib_ratio_and_close_matches():
    rnd = random.Random(7)
    words = ["ohio state", "indiana", "mississippi state", "lsu", "miss st", "western michigan", "w michigan",
             "north carolina", "unc", "texas tech", "tex tech", "aabbaa", "abcabcab", "the u"]
    pairs = [(a, b) for a in words for b in words] + [
        ("".join(rnd.choice("abc ") for _ in range(rnd.randint(0, 12))), "".join(rnd.choice("abc ") for _ in range(rnd.randint(0, 12))))
        for _ in range(200)]
    res = js([("ratio", [a, b]) for a, b in pairs] + [("getCloseMatch", [w, words, 0.8]) for w in words + ["ohio st", "indianna", "lsu tigers"]])
    for (a, b), r in zip(pairs, res):
        assert abs(r["value"] - difflib.SequenceMatcher(None, a, b).ratio()) < 1e-12, (a, b)
    for w, r in zip(words + ["ohio st", "indianna", "lsu tigers"], res[len(pairs):]):
        best = difflib.get_close_matches(w, words, n=1, cutoff=0.8)
        assert r["value"] == (best[0] if best else None), w


def test_parse_card_match_and_props():
    variants = [CARD, CARD.replace("Ohio State, U", "OHIO ST, O"), CARD.replace("(+3.5)", "(PK)"),
                CARD.replace("DUKE, U", "UNC, U").replace("yards?\nOVER", "yards? OVER").replace("sacks against Arizona State?\nUNDER", "sacks against Arizona State? UNDER"),
                "Ohio State @ Indiana (+3.5) o/u 50.5 *5 Point Game*\nMississippi State @ LSU (-11.5) o/u 58.5\n"
                "Questions\n1. Will they rush for over or under 150.5 yards?\nCombined Score -",
                "Ohio State @ Indiana (+3.5) o/u 50.5\nBUCKEYES, U\nQuestions\n"
                "1. Ohio State and Indiana meet again. Which team will have more rushing yards?\nINDIANA\n"
                "2. Which QB will end the game with more passing yards? (Sayin or Mendoza)\nmendoza\n"
                "3. Will Indiana and Ohio State combine for over or under 2.5 turnovers? UNDER\n"
                "Combined Score - \n318"]
    wg = week_games(7)
    calls = []
    expected = []
    for text in variants:
        parsed = pool.parse_card(text)
        expected.append(parsed)
        calls.append(("parseCard", [text]))
        games = copy.deepcopy(parsed["games"])
        expected.append(pool.match_games(games, wg, REF))
        calls.append(("matchGames", [parsed["games"], wg, REF, NICK]))
        for p in parsed["props"]:
            expected.append(pool.interpret_prop(p, wg, REF))
            calls.append(("interpretProp", [p, wg, REF, NICK]))
    res = js(calls)
    for exp, got, call in zip(expected, res, calls):
        assert got["ok"], got
        assert same(exp, got["value"]), call[0]


def test_which_team_and_parse_entry():
    pairs = [("UNC", ["North Carolina", "Duke"]), ("MISS ST", ["Mississippi State", "LSU"]), ("TENN", ["Alabama", "Tennessee"]),
             ("WESTERN MICH", ["Western Michigan", "Central Michigan"]), ("wmu", ["Western Michigan", "Central Michigan"]),
             ("OLE MISS", ["Missouri", "Ole Miss"]), ("ND", ["Notre Dame", "BYU"]), ("tech", ["Texas Tech", "Arizona State"]),
             ("Wazzu", ["Washington State", "Oregon State"]), ("sparty", ["Michigan State", "Northwestern"]), ("zzz", ["A", "B"])]
    items = [{"idx": i, "kind": g["kind"], "data": g} for i, g in enumerate(pool.parse_card(CARD)["games"])]
    items += [{"idx": len(items) + i, "kind": "prop", "data": p} for i, p in enumerate(pool.parse_card(CARD)["props"])]
    entries = [CARD, CARD.replace("TENN, U", "ALABAMA, U").replace("Combined Score - 347", "Combined Score - 338"),
               CARD.replace("DUKE, U", "UNC, O").replace("MISSOURI, O", "OLE MISS, U"),
               CARD.replace("Ohio State, U", "").replace("Combined Score - 347", "Combined Score -"),
               "Wisconsin @ UCLA (-2.5) o/u 46.4\nUCLA, O\nQuestions\n7. something over or under 3.5 sacks\nOVER"]
    calls = [("whichTeam", [s, c, REF, NICK]) for s, c in pairs] + [("parseEntry", [t, items, REF, NICK]) for t in entries]
    expected = [pool.which_team(s, c, REF) for s, c in pairs] + [pool.parse_entry(t, items, REF) for t in entries]
    for exp, got, call in zip(expected, js(calls), calls):
        assert got["ok"], got
        assert same(exp, got["value"]), (call, exp, got)


def _items():
    """A graded week: card items with games, props with results."""
    parsed = pool.parse_card(CARD)
    finals = [(24, 28), (35, 17), (21, 24), (31, 30), (20, 17), (17, 24), (20, 17), (24, 10)]
    items = []
    for i, g in enumerate(parsed["games"]):
        hp, ap = finals[i]
        items.append({"idx": i, "kind": g["kind"], "data": {**g, "flipped": i == 2},
                      "game": {"completed": 1, "home_points": hp, "away_points": ap}})
    for j, p in enumerate(parsed["props"]):
        items.append({"idx": len(items), "kind": "prop", "data": p, "prop_actual": [162, 2.5, 690][j]})
    return items


def _entries(items):
    texts = {"Chad O'Connor": CARD,
             "Ron O’Connor": CARD.replace("TENN, U", "ALABAMA, U").replace("Combined Score - 347", "Combined Score - 338"),
             "Patrick Kenney": CARD.replace("Ohio State, U", "INDIANA, O").replace("Combined Score - 347", "Combined Score - 360"),
             "Zach Kenney": CARD.replace("BYU, U", "NOTRE DAME, O").replace("Combined Score - 347", "Combined Score - 351"),
             "Kevin Kenney": CARD.replace("WISCONSIN, U", "UCLA, U").replace("Combined Score - 347", "Combined Score - 329")}
    out = []
    for n, (name, t) in enumerate(texts.items()):
        e = pool.parse_entry(t, items, REF)
        out.append({"player": name, "picks": e["picks"], "tiebreaker": e["tiebreaker"], "reported_points": [14, 12, None, 17, 9][n]})
    return out


def test_league_scoring_sheets_and_inference():
    items = _items()
    entries = _entries(items)
    configs = [league.DEFAULT_SCORING, {**league.DEFAULT_SCORING, "bonus_game_mode": "both", "tiebreak": {"type": "closest", "points": 2},
                                        "potw_ties": "closest_combined"},
               {**league.DEFAULT_SCORING, "bonus_game_mode": "ats_plus", "tiebreak": {"type": "tiers", "points": [3, 2, 1]}},
               {**league.DEFAULT_SCORING, "tiebreak": {"type": "within", "points": 1, "margin": 10}}]
    incomplete = copy.deepcopy(items)
    incomplete[0]["game"]["completed"] = 0
    calls, expected = [], []
    for cfg in configs:
        for its in (items, incomplete):
            sw = league.score_week(its, entries, cfg)
            expected.append(sw)
            calls.append(("scoreWeek", [its, entries, cfg]))
            expected.append(league.week_sheet(its, sw, entries, league.DEFAULT_HIGHLIGHTS))
            calls.append(("weekSheet", [its, sw, entries, league.DEFAULT_HIGHLIGHTS]))
    weeks = [{"week": 6, "rows": league.score_week(items, entries[:3], configs[1])["rows"]},
             {"week": 7, "rows": league.score_week(items, entries, configs[0])["rows"]}]
    standings = league.leaderboard(weeks)
    expected += [standings, league.season_sheet(standings, weeks, league.DEFAULT_HIGHLIGHTS),
                 league.infer_rules([{"week": 7, "items": items, "entries": entries}], league.DEFAULT_SCORING),
                 [c for c in league.candidate_configs(league.DEFAULT_SCORING)]]
    calls += [("leaderboard", [weeks]), ("seasonSheet", [standings, weeks, league.DEFAULT_HIGHLIGHTS]),
              ("inferRules", [[{"week": 7, "items": items, "entries": entries}], league.DEFAULT_SCORING]),
              ("candidateConfigs", [league.DEFAULT_SCORING])]
    for exp, got, call in zip(expected, js(calls), calls):
        assert got["ok"], got
        assert same(exp, got["value"]), call[0]


def test_export_and_grading_helpers():
    parsed = pool.parse_card(CARD)
    card = [{**g, "idx": i} for i, g in enumerate(parsed["games"])]
    picks = {i: {"ats": ["home", "away", None][i % 3], "ou": ["over", None, "under"][i % 3]} for i in range(len(card))}
    props = [{**p, "pick": ["over", None, "under"][i]} for i, p in enumerate(parsed["props"])]
    cases = [(grading.grade_ats, "gradeAts", ["home", -3.5, 24, 20]), (grading.grade_ats, "gradeAts", ["away", 3, 20, 23]),
             (grading.grade_ou, "gradeOu", ["over", 44.5, 24, 20]), (grading.grade_ou, "gradeOu", ["under", 44, 24, 20]),
             (grading.format_spread, "formatSpread", ["Indiana", 3.5]), (grading.format_spread, "formatSpread", ["Indiana", -7.0]),
             (grading.format_spread, "formatSpread", ["Indiana", 0]), (pool.grade_prop, "gradeProp", ["over", 150.5, 162])]
    calls = [(n, a) for _, n, a in cases]
    expected = [f(*a) for f, _, a in cases]
    # JSON turns dict keys into strings; the JS side indexes picks[g.idx], which works for both
    calls.append(("exportText", [card, {str(k): v for k, v in picks.items()}, props, 347]))
    expected.append(pool.export_text(card, picks, props, 347))
    for exp, got, call in zip(expected, js(calls), calls):
        assert got["ok"], got
        assert same(exp, got["value"]), (call, exp, got)


def test_review_stats_and_calibration():
    items = _items()
    rnd = random.Random(3)
    for it in items:
        if it["kind"] == "prop":
            it["claude"] = {"pick": rnd.choice(["over", "under"]), "confidence": rnd.randint(50, 75), "projection": 10.5}
            it["claude_prop_result"] = rnd.choice(["W", "L", "P", None])
            it["prop_result"] = rnd.choice(["W", "L"])
            it["my_prop"] = rnd.choice(["over", "under"])
        else:
            it["claude"] = {"ats_pick": rnd.choice(["home", "away"]), "ou_pick": rnd.choice(["over", "under", None]),
                            "confidence": rnd.randint(50, 75), "proj_home": 24, "proj_away": 21, "reasoning": "r"}
            it["claude_ats_result"], it["claude_ou_result"] = rnd.choice("WLP"), rnd.choice("WL")
            it["ats_result"], it["ou_result"] = rnd.choice("WL"), rnd.choice("WLP")
            it["my_ats"], it["my_ou"] = rnd.choice(["home", "away"]), rnd.choice(["over", "under"])
            it["market"] = {"home_spread": it["data"]["home_spread"] + rnd.choice([-2, 0, 1.5]),
                            "total": (it["data"].get("total") or 50) + rnd.choice([-3, 0, 2])}
    rows = review.week_items(items)
    big = rows * 4
    s = review.stats(big)
    calls = [("weekItems", [items]), ("stats", [rows]), ("stats", [big]), ("calibrationLines", [s])]
    expected = [rows, review.stats(rows), s, review.calibration_lines(s)]
    for exp, got, call in zip(expected, js(calls), calls):
        assert got["ok"], got
        assert same(exp, got["value"]), (call[0], exp, got["value"])


def test_prompt_pieces_and_answers():
    g = {"id": 1, "season": 2026, "week": 7, "season_type": "regular", "home_team": "Indiana", "away_team": "Ohio State",
         "home_conf": "Big Ten", "away_conf": None, "neutral_site": 0, "venue": "Memorial Stadium",
         "start_date": "2026-10-17T19:30:00.000Z", "spread": 3.5, "total": 50.0, "spread_open": 4.0, "total_open": None}
    market = {"home": "Indiana", "spread": 4.5, "total": 49.5, "label": "opening line (replay: closing line hidden)"}
    answers = ['```json\n[{"item": 1, "a": 2}, {"item": 3}]\n```', 'Sure. {"pick": "over"} ok', '“x” [ {"item": 2} ]',
               '{"items": [{"item": 4}]}', 'text [1, 2] then {"a": [1, {"b": "]"}]}']
    data = {"ats_pick": "ohio state", "ou_pick": "UNDER", "projected_home_score": 20.4, "projected_away_score": "27",
            "confidence": 99, "reasoning": " r ", "key_factors": ["a", " ", "b", "c", "d"]}
    calls = [("headerLines", [g, market]), ("headerLines", [{**g, "spread": None, "total": None, "neutral_site": 1}, None]),
             ("researchBlock", [["Indiana", "Ohio State", "Indiana"], "2026-10-16T19:00+00:00", True]),
             ("researchBlock", [["Duke"], None, False]), ("rowFromData", [g, data, "m"]), ("propFromData", [{"pick": "Over", "confidence": 40}, "m"])]
    calls += [("extractJson", [a]) for a in answers]
    expected = [predict.header_lines(g, {"market_now": market}), predict.header_lines({**g, "spread": None, "total": None, "neutral_site": 1}),
                manual.research_block(["Indiana", "Ohio State", "Indiana"], "2026-10-16T19:00+00:00", True),
                manual.research_block(["Duke"], None, False)]
    row = predict.row_from_data(g, data, model="m")
    expected.append({"ats_pick": row["ats_pick"], "ou_pick": row["ou_pick"], "proj_home": row["proj_home"], "proj_away": row["proj_away"],
                     "confidence": row["confidence"], "reasoning": row["reasoning"], "key_factors": json.loads(row["key_factors"]),
                     "conditions_impact": row["conditions_impact"], "model": "m"})
    expected.append(predict.prop_from_data({"pick": "Over", "confidence": 40}, "m"))
    expected += [manual.extract_json(a) for a in answers]
    for exp, got, call in zip(expected, js(calls), calls):
        assert got["ok"], got
        assert same(exp, got["value"]), (call[0], exp, got["value"])
    bad = js([("rowFromData", [g, {**data, "ats_pick": "Nobody"}, "m"]), ("extractJson", ["no json"])])
    assert not bad[0]["ok"] and "neither" in bad[0]["error"] and not bad[1]["ok"]
