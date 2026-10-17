"""build_site.py end to end against the fake CFBD / Open-Meteo server (tests/fake_services.py)."""
import json
import os
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer

import pytest

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(__file__))


@pytest.fixture(scope="module")
def fake_url():
    import fake_services
    srv = ThreadingHTTPServer(("127.0.0.1", 0), fake_services.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def run_build(fake_url, tmp, *args):
    env = {**os.environ, "CFBD_BASE_URL": fake_url, "OPEN_METEO_URL": f"{fake_url}/v1/forecast",
           "OPEN_METEO_PREVIOUS_RUNS_URL": f"{fake_url}/v1/forecast", "OPEN_METEO_ARCHIVE_URL": f"{fake_url}/v1/archive",
           "NCAA_API_URL": fake_url, "CFBD_API_KEY": "test", "SITE_CACHE_DIR": str(tmp / "cache"),
           "SITE_DATA_DIR": str(tmp / "data"), "PICKS_CALIBRATION_PATH": str(tmp / "cal" / "calibration.json"),
           "PICKS_HFA_PATH": str(tmp / "cal" / "hfa.json")}
    for k in ("CFB_DB_PATH", "BACKTEST_DB_PATH", "BACKTEST_CACHE_DIR"):
        env.pop(k, None)
    os.makedirs(tmp / "cal", exist_ok=True)
    p = subprocess.run([sys.executable, os.path.join(ROOT, "build_site.py"), *args], env=env, capture_output=True,
                       text=True, timeout=300)
    assert p.returncode == 0, p.stdout + p.stderr
    return p.stdout


def test_build_site(fake_url, tmp_path):
    out = run_build(fake_url, tmp_path, "--weeks", "7")
    data = tmp_path / "data"
    index = json.loads((data / "index.json").read_text())
    assert index["current"] == {"week": 7, "season_type": "regular"}
    assert {w["week"] for w in index["weeks"]} == {6, 7, 8}

    w7 = json.loads((data / "2026" / "regular-w07.json").read_text())
    assert w7["frozen"] and w7["deadline"] == "2026-10-16T19:00+00:00"   # Friday noon Pacific
    osu = next(g for g in w7["games"] if g["away_team"] == "Ohio State")
    ctx = osu["ctx"]
    assert ctx["replay"] and ctx["frozen"] and ctx["as_of"].startswith("2026-10-16T19:00")
    assert ctx["line"]["label"].startswith("opening line") and ctx["line"]["spread"] == osu["spread_open"]
    assert "Team analytics" in ctx["text"] and "Betting history" in ctx["text"] and "Information cutoff" in ctx["text"]
    assert ctx["prop_stats"]["home"]["team"] == "Indiana" and ctx["prop_stats"]["home"]["for"]["rush_yds"]["n"] >= 4

    w6 = json.loads((data / "2026" / "regular-w06.json").read_text())
    assert w6["complete"] and all(g["box"] and g["box"]["home"]["rush_yds"] is not None for g in w6["games"])

    w8 = json.loads((data / "2026" / "regular-w08.json").read_text())
    assert not w8["frozen"] and all(g["ctx"] and not g["ctx"]["replay"] for g in w8["games"])
    assert w8["games"][0]["ctx"]["weather_text"]

    prompts = json.loads((data / "prompts.json").read_text())
    assert "handicapper" in prompts["system"] and "item" in prompts["game_spec"]
    ref = json.loads((data / "reference-2026.json").read_text())
    assert ref["fbs"]["total"] == 138 and "unc" in ref["nicknames"]["North Carolina"]

    # Second run: frozen weeks are kept as they were and nothing is fetched twice.
    before = (data / "2026" / "regular-w07.json").read_text()
    out = run_build(fake_url, tmp_path)
    assert "CFBD calls this run: 0 live" in out
    after = json.loads((data / "2026" / "regular-w07.json").read_text())
    assert [g["ctx"]["text"] for g in after["games"]] == [g["ctx"]["text"] for g in json.loads(before)["games"]]
