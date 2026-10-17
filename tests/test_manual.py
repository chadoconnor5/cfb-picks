"""Copy-for-Claude mode: prompts out, pasted answers in."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import manual  # noqa: E402
from test_pool import CARD, WEEK, client  # noqa: E402,F401


def test_mode_defaults(monkeypatch):
    monkeypatch.delenv("PICKS_CLAUDE_MODE", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert manual.mode() == "manual"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert manual.mode() == "api"
    monkeypatch.setenv("PICKS_CLAUDE_MODE", "manual")
    assert manual.mode() == "manual"


def test_extract_json_lenient():
    assert manual.extract_json('Here you go:\n```json\n[{"item": 1}]\n```\nGood luck!') == [{"item": 1}]
    assert manual.extract_json('Sure. {"pick": "over"} hope it helps') == {"pick": "over"}
    assert manual.extract_json('“no” [ {"item": 2} ]') == [{"item": 2}]
    with pytest.raises(ValueError):
        manual.extract_json("no json here")
    assert manual.answers_by_item({"items": [{"item": 3}, {"item": 5}]}).keys() == {3, 5}


def test_card_prompt_and_answer(client):
    client.post("/api/pool/import", json={**WEEK, "text": CARD})
    assert client.get("/api/config").json["claude_mode"] in ("manual", "api")
    card = client.get("/api/pool", query_string=WEEK).json
    cid = card["card"]["id"]
    p = client.get(f"/api/pool/cards/{cid}/prompt").json
    assert "### Item 1" in p["prompt"] and "Answer format" in p["prompt"] and "Indiana" in p["prompt"]
    assert "RESEARCH FIRST" in p["prompt"] and not p["replay"]
    assert len(p["items"]) == 11

    answer = [
        {"item": 1, "ats_pick": "Ohio State", "ou_pick": "over", "projected_home_score": 20, "projected_away_score": 30,
         "confidence": 64, "reasoning": "r", "key_factors": ["a"], "conditions_impact": "Not a factor."},
        {"item": 8, "ats_pick": "home", "ou_pick": None, "projected_home_score": 28, "projected_away_score": 21,
         "confidence": 99, "reasoning": "r"},
        {"item": 9, "pick": "under", "projection": 120, "confidence": 57, "reasoning": "r"},
        {"item": 2, "ats_pick": "Nobody", "ou_pick": "over", "projected_home_score": 1, "projected_away_score": 2,
         "confidence": 55, "reasoning": "r"},
    ]
    r = client.post(f"/api/pool/cards/{cid}/answer", json={"answer": "```json\n" + json.dumps(answer) + "\n```"}).json
    assert sorted(r["saved"]) == [1, 8, 9] and len(r["errors"]) == 1 and "Item 2" in r["errors"][0]
    items = {n: it for n, it in enumerate(client.get("/api/pool", query_string=WEEK).json["items"], 1)}
    assert items[1]["claude"]["ats_pick"] == "away" and items[1]["claude"]["model"] == manual.MANUAL_MODEL
    assert items[8]["claude"]["confidence"] == 95 and items[8]["claude"]["ou_pick"] is None
    assert items[9]["claude"]["pick"] == "under" and items[9]["claude"]["pool_line"] == 150.5
    assert not items[1]["claude_stale"]

    bad = client.post(f"/api/pool/cards/{cid}/answer", json={"answer": "I think Ohio State"})
    assert bad.status_code == 400


def test_slate_game_prompt_and_answer(client):
    import db
    with db.tx() as conn:
        conn.execute("UPDATE games SET spread=3.5, total=50.5 WHERE id=1")
    p = client.post("/api/games/1/prompt", json={"notes": "QB questionable"}).json["prompt"]
    assert "QB questionable" in p and "Indiana" in p
    r = client.post("/api/games/1/answer", json={"answer": json.dumps(
        {"ats_pick": "Indiana", "ou_pick": "under", "projected_home_score": 27, "projected_away_score": 20,
         "confidence": 60, "reasoning": "r"})}).json
    assert r["ats_pick"] == "home" and r["model"] == manual.MANUAL_MODEL
