"""Style-vs-weather assessment.

Turns a kickoff forecast into severity levels, then scores how exposed each team's offensive
style is to those conditions, adjusted for whether it can adapt (an efficient run game) and for
the opponent's defense. The output is a transparent heuristic for Claude and the UI, not a
point-spread model: it flags who is exposed and why, and Claude weighs it with everything else.

Rules of thumb encoded here:
- Wind is the biggest weather factor: it hurts passing (deep balls most), kicking, and punting.
- Rain and snow hurt ball security, footing, and passing accuracy; they favor run-first teams
  and stout run defenses, and tend to lower scoring.
- Extreme cold mostly shows up in kicking and ball handling; heat in depth/fatigue.
"""
from analytics import _ord

LEVELS = ["none", "mild", "moderate", "severe"]


def _lvl(n):
    return LEVELS[max(0, min(3, n))]


def classify(w, dome=False):
    """Severity per factor from a forecast_window() summary."""
    if dome:
        return {"dome": True, "wind": "none", "precip": "none", "precip_type": None, "temp": "none",
                "passing_hostility": 0.0, "level": "none"}
    if not w:
        return None

    s, g = w.get("wind_mph") or 0, w.get("gust_mph") or 0
    wind = 3 if (s >= 20 or g >= 35) else 2 if (s >= 15 or g >= 28) else 1 if (s >= 10 or g >= 20) else 0

    snow = w.get("snow_in") or 0
    rain_total, rain_max = w.get("precip_in") or 0, w.get("precip_max_hr_in") or 0
    prob = w.get("precip_prob") or 0
    if snow >= 0.1:
        ptype = "snow"
        precip = 3 if snow >= 2 else 2 if snow >= 0.5 else 1
    else:
        ptype = "rain"
        precip = (3 if (rain_max >= 0.3 or rain_total >= 0.75)
                  else 2 if (rain_max >= 0.1 or rain_total >= 0.25)
                  else 1 if (rain_total >= 0.03 or prob >= 60) else 0)
        if precip == 0:
            ptype = None

    tmin, tmax = w.get("temp_min_f"), w.get("temp_max_f")
    temp, temp_kind = 0, None
    if tmin is not None and tmin <= 20:
        temp, temp_kind = 3, "cold"
    elif tmin is not None and tmin <= 32:
        temp, temp_kind = 2, "cold"
    elif tmax is not None and tmax >= 90:
        temp, temp_kind = 2, "heat"

    # How hard the conditions are on passing games (0 = perfect, ~6 = brutal).
    hostility = [0, 1, 2, 3][wind] + [0, 0.5, 1.5, 2.5][precip] + (0.5 if ptype == "snow" and precip else 0) \
        + (0.5 if temp_kind == "cold" and temp == 3 else 0)
    level = "none" if hostility < 1 else "mild" if hostility < 2 else "significant" if hostility < 3.5 else "extreme"
    return {"dome": False, "wind": _lvl(wind), "precip": _lvl(precip), "precip_type": ptype,
            "temp": _lvl(temp), "temp_kind": temp_kind, "passing_hostility": round(hostility, 1), "level": level}


def _centered(pct):
    """Map a 0-100 percentile to -1..1 (50th pct = 0)."""
    return 0.0 if pct is None else (pct - 50) / 50


def team_exposure(team, opp):
    """How dependent a team's offense is on conditions that bad weather takes away.

    Positive = pass-dependent (hurt by wind/rain/snow); negative = run-first (insulated).
    """
    if not team:
        return None, []
    pct, opct = team["pct"], (opp or {}).get("pct", {})
    reasons = []
    score = _centered(pct["pass_rate"])
    o = team["off"]
    if o["pass_rate"] is not None:
        reasons.append(f"passes on {o['pass_rate'] * 100:.0f}% of plays ({_ord(pct['pass_rate'])})")

    if (pct.get("pass_expl") or 0) >= 70:
        score += 0.3
        reasons.append(f"leans on explosive passing ({_ord(pct['pass_expl'])})")

    rush, pass_ = pct.get("rush_ppa"), pct.get("pass_ppa")
    if rush is not None and pass_ is not None:
        if score > 0 and rush >= 65:
            score -= 0.3
            reasons.append(f"has an efficient run game to fall back on (rush EPA {_ord(rush)})")
        elif score > 0 and rush <= 35:
            score += 0.2
            reasons.append(f"weak run game if forced to lean on it (rush EPA {_ord(rush)})")
        elif score < 0 and rush <= 35:
            score += 0.3
            reasons.append(f"run-first but inefficient on the ground (rush EPA {_ord(rush)})")

    opp_run_d = opct.get("def_rush_ppa")
    if opp_run_d is not None and score < 0 and opp_run_d >= 75:
        score += 0.3
        reasons.append(f"faces a stout run defense ({_ord(opp_run_d)} vs rush)")
    elif opp_run_d is not None and score < 0 and opp_run_d <= 30:
        score -= 0.15
        reasons.append(f"faces a soft run defense ({_ord(opp_run_d)} vs rush)")
    return round(score, 2), reasons


SMALL_SAMPLE = 30


def _n_flag(n):
    return f"n={n}" + (", small sample" if n < SMALL_SAMPLE else "")


def historical(cal, cond, teams, edge, total_lean):
    """Evidence lines from backtest calibration for conditions like these."""
    lines = []
    base = cal.get("baseline_under_pct")

    def scoring(label, b):
        if b and b.get("n_games") and b.get("under_pct") is not None:
            lines.append(f"Past games with {label} ({_n_flag(b['under_n'])}): {b['pts_vs_total']:+.1f} pts vs closing total, "
                         f"unders {b['under_pct']}% (all games {base}%).")

    if cond["wind"] != "none":
        scoring(f"{cond['wind']} wind", cal.get("wind", {}).get(cond["wind"]))
    if cond.get("precip_type"):
        scoring(f"{cond['precip']} {cond['precip_type']}", cal.get("precip", {}).get(f"{cond['precip_type']}:{cond['precip']}"))
    by_style = cal.get("level", {}).get(cond["level"], {}).get("by_style", {})
    for t in teams.values():
        e = by_style.get(t["style"])
        if e and e.get("n") and e.get("d_off_ppa") is not None:
            pr = f", pass rate {e['d_pass_rate'] * 100:+.0f} pts" if e.get("d_pass_rate") is not None else ""
            lines.append(f"{t['team']}: {t['style']} offenses in {cond['level']} conditions averaged "
                         f"{e['d_off_ppa']:+.2f} EPA/play vs their norm{pr} ({_n_flag(e['n'])}).")
    rules = cal.get("rules", {})
    if edge and rules.get("edge_ats", {}).get("n"):
        r = rules["edge_ats"]
        lines.append(f"The style-edge side has covered {r['pct']}% historically ({_n_flag(r['n'])}).")
    if total_lean and rules.get("under_lean", {}).get("n"):
        r = rules["under_lean"]
        lines.append(f"Games flagged 'lean under' went under {r['pct']}% ({_n_flag(r['n'])}).")
    return lines


def assess(home, away, weather, cond, home_name, away_name, calibration=None):
    """Combine profiles + conditions into a summary dict for the prompt and UI."""
    result = {"conditions": cond, "weather": weather, "teams": {}, "notes": [], "total_lean": None, "edge": None,
              "historical": []}
    if cond is None:
        result["notes"].append("No kickoff forecast available yet (forecasts start ~15 days out).")
    elif cond["dome"]:
        result["notes"].append("Indoors: weather is not a factor.")

    hostile = cond is not None and cond["passing_hostility"] >= 1
    for side, team, opp, name in (("home", home, away, home_name), ("away", away, home, away_name)):
        score, reasons = team_exposure(team, opp)
        if score is None:
            label = "unknown"
        elif not hostile:
            label = "unaffected"
        elif score >= 0.35:
            label = "exposed"
        elif score <= -0.35:
            label = "insulated"
        else:
            label = "neutral"
        result["teams"][side] = {"team": name, "style": (team or {}).get("style", "unknown"),
                                 "exposure_score": score, "label": label, "reasons": reasons}

    if not hostile:
        return result

    if cond["wind"] in ("moderate", "severe"):
        result["notes"].append("Wind will limit the deep passing game and makes field goals, PATs and punts less reliable.")
    if cond["precip"] in ("moderate", "severe"):
        what = "Snow" if cond["precip_type"] == "snow" else "Rain"
        result["notes"].append(f"{what}: expect more fumbles and dropped passes and worse footing, which favors running teams and strong run defenses.")
    if cond.get("temp_kind") == "cold" and cond["temp"] != "none":
        result["notes"].append("Cold weather shortens kicks and makes the ball harder to handle.")

    labels = {s: t["label"] for s, t in result["teams"].items()}
    if labels.get("home") == "exposed" and labels.get("away") in ("insulated", "neutral"):
        result["edge"] = "away"
    elif labels.get("away") == "exposed" and labels.get("home") in ("insulated", "neutral"):
        result["edge"] = "home"
    if result["edge"]:
        t = result["teams"][result["edge"]]["team"]
        result["notes"].append(f"Style edge in these conditions: {t}.")

    if cond["level"] in ("significant", "extreme"):
        result["total_lean"] = "under"
        result["notes"].append("Conditions this bad usually push scoring down, so lean under unless the total already accounts for it.")
    if calibration:
        result["historical"] = historical(calibration, cond, result["teams"], result["edge"], result["total_lean"])
    return result


def describe(a):
    """Text block for the prompt."""
    lines = []
    w, c = a.get("weather"), a.get("conditions")
    if c and c.get("dome"):
        lines.append("  Indoors (dome).")
    elif w:
        parts = [f"{w['temp_f']:.0f}F" if w.get("temp_f") is not None else None,
                 f"wind {w['wind_mph']:.0f} mph (gusts {w['gust_mph']:.0f})" if w.get("wind_mph") is not None else None,
                 f"precip {w['precip_in']:.2f} in over game window, {w['precip_prob']:.0f}% max chance" if w.get("precip_in") is not None else None,
                 f"snow {w['snow_in']:.1f} in" if w.get("snow_in") else None]
        lines.append("  Forecast at kickoff: " + ", ".join(p for p in parts if p))
        lines.append(f"  Severity: wind {c['wind']}, precipitation {c['precip']}"
                     + (f" ({c['precip_type']})" if c.get("precip_type") else "")
                     + f", temperature {c['temp']}. Passing-hostility {c['passing_hostility']}/6 ({c['level']}).")
    for side in ("away", "home"):
        t = a["teams"].get(side)
        if t:
            lines.append(f"  {t['team']}: {t['style']}, weather exposure {t['label']}"
                         + (f" (score {t['exposure_score']:+.2f})" if t["exposure_score"] is not None else "")
                         + (" - " + "; ".join(t["reasons"]) if t["reasons"] else ""))
    lines.extend(f"  - {n}" for n in a["notes"])
    if a.get("historical"):
        lines.append("  Historical evidence (backtest of past FBS games, actual weather vs closing lines):")
        lines.extend(f"  * {h}" for h in a["historical"])
    return "\n".join(lines)
