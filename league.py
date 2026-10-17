"""League scoring and leaderboard.

Known rules (defaults, all editable):
  - correct pick against the spread: 3 pts
  - correct over/under: 1 pt
  - correct question (prop): 1 pt
  - mid-major game against the spread: 2 pts
  - N-Point Game: the spread pick is worth N (assumption; "both" mode also makes the O/U worth N)
  - Player of the Week: +3 to the week's high score
Unknown (to infer from the league's posted points):
  - how the combined-score guess (sum of final scores of all card games, mid-major excluded) earns points
  - how Player of the Week ties are broken
"""
import itertools
import json

from grading import grade_ats, grade_ou

DEFAULT_SCORING = {
    "ats": 3, "ou": 1, "prop": 1, "mid_major": 2,
    "bonus_game_mode": "ats",          # "ats": spread pick worth N; "both": spread and O/U worth N each; "ats_plus": 3 + N
    "tiebreak": {"type": "none"},      # none | closest {points} | tiers {points: [..]} | exact {points} | within {points, margin}
    "potw_bonus": 3,
    "potw_ties": "all",                # all tied get the bonus | closest_combined: combined-score guess breaks the tie
}


def merged(config):
    out = json.loads(json.dumps(DEFAULT_SCORING))
    for k, v in (config or {}).items():
        out[k] = v
    return out


def actual_combined(items):
    """Sum of final scores of all card games (mid-major excluded). None until every one is final."""
    games = [it for it in items if it["kind"] == "game"]
    if not games or any(not (it.get("game") or {}).get("completed") for it in games):
        return None
    return sum(it["game"]["home_points"] + it["game"]["away_points"] for it in games)


def _game_result(item, side, total_pick):
    g = item.get("game") or {}
    if not g.get("completed") or g.get("home_points") is None:
        return None, None
    d = item["data"]
    hp, ap = g["home_points"], g["away_points"]
    if d.get("flipped"):
        hp, ap = ap, hp
    ats = grade_ats(side, d["home_spread"], hp, ap) if side else None
    ou = grade_ou(total_pick, d.get("total"), hp, ap) if total_pick and d.get("total") is not None else None
    return ats, ou


def _prop_result(item, pick):
    actual, line = item.get("prop_actual"), item["data"].get("line")
    if pick not in ("over", "under") or actual is None or line is None:
        return None
    if actual == line:
        return "P"
    return "W" if (actual > line) == (pick == "over") else "L"


def score_entry(items, picks, cfg):
    """picks: {idx: {"ats": "home"|"away", "ou": "over"|"under"} or {"pick": "over"|"under"}}.
    Returns base points (before tiebreak / POTW) with a breakdown."""
    b = {"ats": 0, "ou": 0, "prop": 0, "mid_major": 0, "bonus_game": 0, "correct": 0, "graded": 0}
    detail = []
    for it in items:
        p = picks.get(str(it["idx"])) or picks.get(it["idx"]) or {}
        d = it["data"]
        if it["kind"] == "prop":
            r = _prop_result(it, p.get("pick"))
            pts = cfg["prop"] if r == "W" else 0
            b["prop"] += pts
            detail.append({"idx": it["idx"], "kind": "prop", "pick": p.get("pick"), "result": r, "points": pts})
        else:
            ats, ou = _game_result(it, p.get("ats"), p.get("ou"))
            w = d.get("weight", 1)
            if it["kind"] == "mid_major":
                ats_pts = cfg["mid_major"] if ats == "W" else 0
                b["mid_major"] += ats_pts
                ou_pts = 0
            else:
                if w > 1 and cfg["bonus_game_mode"] == "ats":
                    ats_val, ou_val = w, cfg["ou"]
                elif w > 1 and cfg["bonus_game_mode"] == "both":
                    ats_val, ou_val = w, w
                elif w > 1 and cfg["bonus_game_mode"] == "ats_plus":
                    ats_val, ou_val = cfg["ats"] + w, cfg["ou"]
                else:
                    ats_val, ou_val = cfg["ats"], cfg["ou"]
                ats_pts = ats_val if ats == "W" else 0
                ou_pts = ou_val if ou == "W" else 0
                if w > 1:
                    b["bonus_game"] += ats_pts + ou_pts
                else:
                    b["ats"] += ats_pts
                    b["ou"] += ou_pts
            detail.append({"idx": it["idx"], "kind": it["kind"], "ats": p.get("ats"), "ou": p.get("ou"),
                           "ats_result": ats, "ou_result": ou, "points": ats_pts + ou_pts})
        for r in ([detail[-1].get("result")] if it["kind"] == "prop" else [detail[-1]["ats_result"], detail[-1]["ou_result"]]):
            if r in ("W", "L", "P"):
                b["graded"] += 1
                b["correct"] += r == "W"
    base = b["ats"] + b["ou"] + b["prop"] + b["mid_major"] + b["bonus_game"]
    return {"base": base, "breakdown": b, "detail": detail}


def tiebreak_points(rule, guesses, actual):
    """{player: points} for the combined-score guess under a rule."""
    out = {p: 0 for p in guesses}
    if actual is None or not rule or rule.get("type") in (None, "none"):
        return out
    dist = {p: abs(g - actual) for p, g in guesses.items() if g is not None}
    if not dist:
        return out
    t = rule["type"]
    if t == "closest":
        best = min(dist.values())
        for p, d in dist.items():
            if d == best:
                out[p] = rule.get("points", 1)
    elif t == "exact":
        for p, d in dist.items():
            if d == 0:
                out[p] = rule.get("points", 1)
    elif t == "within":
        for p, d in dist.items():
            if d <= rule.get("margin", 5):
                out[p] = rule.get("points", 1)
    elif t == "tiers":
        ranked = sorted(set(dist.values()))
        pts = rule.get("points", [])
        for p, d in dist.items():
            r = ranked.index(d)
            out[p] = pts[r] if r < len(pts) else 0
    return out


def week_complete(items):
    """Every game final and every question resolved (props can be resolved by hand)."""
    if not items:
        return False
    for it in items:
        if it["kind"] == "prop":
            if it.get("prop_actual") is None:
                return False
        elif not (it.get("game") or {}).get("completed"):
            return False
    return True


def score_week(items, entries, cfg):
    """entries: [{player, picks, tiebreaker}] -> rows with totals, tiebreak points, POTW."""
    cfg = merged(cfg)
    actual = actual_combined(items)
    rows = []
    for e in entries:
        s = score_entry(items, e["picks"], cfg)
        rows.append({"player": e["player"], "tiebreaker": e.get("tiebreaker"), **s,
                     "distance": abs(e["tiebreaker"] - actual) if actual is not None and e.get("tiebreaker") is not None else None})
    tb = tiebreak_points(cfg["tiebreak"], {r["player"]: r["tiebreaker"] for r in rows}, actual)
    for r in rows:
        r["tiebreak_points"] = tb.get(r["player"], 0)
        r["pre_bonus"] = r["base"] + r["tiebreak_points"]
    all_graded = bool(rows) and week_complete(items)
    for r in rows:
        r["potw"] = False
    if rows and all_graded:
        top = max(r["pre_bonus"] for r in rows)
        leaders = [r for r in rows if r["pre_bonus"] == top]
        if len(leaders) > 1 and cfg["potw_ties"] == "closest_combined" and actual is not None:
            best = min((r["distance"] for r in leaders if r["distance"] is not None), default=None)
            leaders = [r for r in leaders if r["distance"] == best] if best is not None else leaders
        for r in leaders:
            r["potw"] = True
    for r in rows:
        r["potw_points"] = cfg["potw_bonus"] if r["potw"] else 0
        r["total"] = r["pre_bonus"] + r["potw_points"]
    rows.sort(key=lambda r: (-r["total"], r["distance"] if r["distance"] is not None else 9e9))
    return {"actual_combined": actual, "complete": bool(all_graded), "rows": rows}


def leaderboard(weeks):
    """weeks: [{week, rows}] -> season standings."""
    table = {}
    for w in weeks:
        for r in w["rows"]:
            t = table.setdefault(r["player"], {"player": r["player"], "total": 0, "weeks": 0, "potw": 0, "correct": 0,
                                               "graded": 0, "by_week": {}, "ats": 0, "ou": 0, "prop": 0,
                                               "mid_major": 0, "bonus_game": 0, "tiebreak": 0})
            t["total"] += r["total"]
            t["weeks"] += 1
            t["potw"] += r["potw"]
            t["correct"] += r["breakdown"]["correct"]
            t["graded"] += r["breakdown"]["graded"]
            for k in ("ats", "ou", "prop", "mid_major", "bonus_game"):
                t[k] += r["breakdown"][k]
            t["tiebreak"] += r["tiebreak_points"]
            t["by_week"][w["week"]] = r["total"]
    out = sorted(table.values(), key=lambda t: -t["total"])
    for i, t in enumerate(out):
        t["rank"] = 1 + sum(1 for o in out if o["total"] > t["total"])
        t["avg"] = round(t["total"] / t["weeks"], 2) if t["weeks"] else None
        t["pick_pct"] = round(100 * t["correct"] / t["graded"], 1) if t["graded"] else None
    return out


# ---------- inferring the unknown rules from the league's posted points ----------

def candidate_configs(base_cfg):
    tiebreaks = [{"type": "none"}]
    for pts in (1, 2, 3, 5):
        tiebreaks += [{"type": "closest", "points": pts}, {"type": "exact", "points": pts}]
    for tiers in ([3, 2, 1], [2, 1], [5, 3, 1], [3, 1]):
        tiebreaks.append({"type": "tiers", "points": tiers})
    for pts, margin in itertools.product((1, 2, 3), (5, 10)):
        tiebreaks.append({"type": "within", "points": pts, "margin": margin})
    for bonus_mode, tb, ties in itertools.product(("ats", "both", "ats_plus"), tiebreaks, ("all", "closest_combined")):
        yield merged({**base_cfg, "bonus_game_mode": bonus_mode, "tiebreak": tb, "potw_ties": ties})


def describe_rule(cfg):
    tb = cfg["tiebreak"]
    t = {"none": "no points for the combined score",
         "closest": f"closest combined-score guess gets {tb.get('points')}",
         "exact": f"exact combined-score guess gets {tb.get('points')}",
         "within": f"guesses within {tb.get('margin')} get {tb.get('points')}",
         "tiers": f"closest guesses get {tb.get('points')} by rank"}[tb["type"]]
    b = {"ats": "N-Point Game: spread pick worth N", "both": "N-Point Game: spread and O/U both worth N",
         "ats_plus": "N-Point Game: spread pick worth 3 + N"}[cfg["bonus_game_mode"]]
    p = "Player of the Week ties: all tied get the bonus" if cfg["potw_ties"] == "all" else \
        "Player of the Week ties: broken by combined-score guess"
    return f"{t}; {b}; {p}"


def infer_rules(weeks, base_cfg):
    """weeks: [{week, items, entries (with reported_points)}]. Ranks candidate rule sets by how many
    player-weeks they reproduce exactly."""
    usable = [w for w in weeks if any(e.get("reported_points") is not None for e in w["entries"])]
    if not usable:
        return {"message": "Enter the league's posted weekly points for some players to infer the rules.", "candidates": []}
    results = []
    for cfg in candidate_configs(base_cfg):
        exact = total = 0
        abs_err = 0.0
        for w in usable:
            scored = {r["player"]: r for r in score_week(w["items"], w["entries"], cfg)["rows"]}
            for e in w["entries"]:
                if e.get("reported_points") is None or e["player"] not in scored:
                    continue
                diff = scored[e["player"]]["total"] - e["reported_points"]
                total += 1
                exact += diff == 0
                abs_err += abs(diff)
        if total:
            results.append({"config": cfg, "rule": describe_rule(cfg), "exact": exact, "of": total,
                            "match_pct": round(100 * exact / total, 1), "avg_abs_error": round(abs_err / total, 2)})
    results.sort(key=lambda r: (-r["exact"], r["avg_abs_error"]))
    # residuals under the current settings, to eyeball unexplained points
    residuals = []
    for w in usable:
        scored = {r["player"]: r for r in score_week(w["items"], w["entries"], base_cfg)["rows"]}
        for e in w["entries"]:
            if e.get("reported_points") is not None and e["player"] in scored:
                s = scored[e["player"]]
                residuals.append({"week": w["week"], "player": e["player"], "reported": e["reported_points"],
                                  "computed": s["total"], "unexplained": e["reported_points"] - s["total"],
                                  "guess": e.get("tiebreaker"), "distance": s["distance"], "potw": s["potw"]})
    best = results[0] if results else None
    msg = None
    if best and best["exact"] == 0:
        msg = (f"None of the {len(results)} rule sets reproduces any posted score. The league may use a rule not modeled "
               "here, or some posted points/picks were entered wrong. Check 'Unexplained points' below: a constant gap "
               "points to a missing per-week rule, a gap only for some players points to the combined-score or POTW rule.")
    elif best:
        ties = [r for r in results if r["exact"] == best["exact"] and r["avg_abs_error"] == best["avg_abs_error"]]
        msg = (f"Best fit reproduces {best['exact']} of {best['of']} posted scores ({best['match_pct']}%)."
               + (f" {len(ties)} rule sets fit equally well; more weeks will narrow it down." if len(ties) > 1 else ""))
    return {"message": msg, "candidates": results[:10], "residuals": residuals}



# ---------- sheets + highlights ----------

DEFAULT_HIGHLIGHTS = {
    "Patrick Kenney": "red", "Zach Kenney": "red", "Kevin Kenney": "red", "Darren Kenney": "red",
    "Ron O'Connor": "gold",
}


def norm_name(name):
    return " ".join((name or "").replace("\u2019", "'").replace("\u2018", "'").replace("`", "'").lower().split())


def highlight_for(name, highlights):
    want = norm_name(name)
    for k, v in (highlights or {}).items():
        if norm_name(k) == want:
            return v
    return None


def pick_label(item, pick):
    """Short label for a pick cell, e.g. 'Ohio State U', 'OVER', 'W. Michigan'."""
    if not pick:
        return ""
    if item["kind"] == "prop":
        return (pick.get("pick") or "").upper()
    d = item["data"]
    team = d["home"] if pick.get("ats") == "home" else d["away"] if pick.get("ats") == "away" else "?"
    ou = {"over": "O", "under": "U"}.get(pick.get("ou"))
    return f"{team}{', ' + ou if ou else ''}" if item["kind"] == "game" else team


def week_sheet(items, scored, entries, highlights):
    """Rows of every player's picks per card item with results, for the weekly sheet."""
    by_player = {e["player"]: e for e in entries}
    cols = [{"idx": it["idx"], "kind": it["kind"], "weight": it["data"].get("weight", 1),
             "label": f"Q{it['data'].get('n')}" if it["kind"] == "prop" else f"{it['data']['away']} @ {it['data']['home']}",
             "line": (f"O/U {it['data'].get('line')}" if it["kind"] == "prop" else
                      f"{it['data']['home']} {it['data']['home_spread']:+g}" + (f", o/u {it['data']['total']:g}" if it["data"].get("total") is not None else ""))}
            for it in items]
    rows = []
    for r in scored["rows"]:
        picks = by_player.get(r["player"], {}).get("picks", {})
        det = {d["idx"]: d for d in r["detail"]}
        cells = []
        for it in items:
            d = det.get(it["idx"], {})
            res = d.get("result") if it["kind"] == "prop" else d.get("ats_result")
            cells.append({"label": pick_label(it, picks.get(str(it["idx"])) or picks.get(it["idx"])),
                          "ats_result": res, "ou_result": d.get("ou_result"), "points": d.get("points", 0)})
        rows.append({"player": r["player"], "highlight": highlight_for(r["player"], highlights), "cells": cells,
                     "tiebreaker": r["tiebreaker"], "distance": r["distance"], "breakdown": r["breakdown"],
                     "tiebreak_points": r["tiebreak_points"], "potw": r["potw"], "potw_points": r["potw_points"],
                     "total": r["total"], "reported_points": r.get("reported_points")})
    return {"columns": cols, "rows": rows}


def season_sheet(standings, weeks, highlights):
    leader = standings[0]["total"] if standings else 0
    week_cols = [w["week"] for w in weeks]
    rows = [{**s, "highlight": highlight_for(s["player"], highlights), "behind": leader - s["total"],
             "weeks_cells": [s["by_week"].get(w) for w in week_cols]} for s in standings]
    return {"weeks": week_cols, "rows": rows}


def to_csv(header, rows):
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue()
