"""Weekly pool card: parse the email, match games, interpret props, export picks, grade.

Card format (from the pool email):
    Ohio State @ Indiana (+3.5) o/u 50.5 *5 Point Game*
    Ohio State, U                      <- optional: a pick line (when pasting a reply)
    Questions
    1. ... will they rush for over or under 150.5 yards?
    OVER                               <- optional answer
    Mid-Major Contest
    Western Michigan @ Central Michigan (+6.5)
    WESTERN MICH
    Combined Score - 347

The number in parentheses is the HOME team's spread (Indiana +3.5 means Ohio State -3.5).
"""
import difflib
import re

import teams as teams_mod
from grading import grade_ats, grade_ou

GAME_RE = re.compile(
    r"^\s*(?P<away>[^@\n]+?)\s*(?:@|\bvs\.?)\s*(?P<home>[^(\n]+?)\s*\((?P<spread>[+-]?\s*\d+(?:\.\d+)?|PK|pk|Pick)\)\s*"
    r"(?:o\s*/\s*u\s*(?P<total>\d+(?:\.\d+)?))?\s*(?:\*+\s*(?P<weight>\d+)\s*-?\s*Point\s+Game\s*\*+)?\s*$", re.I)
PICK_RE = re.compile(r"^\s*(?P<team>[^,\n]+?)\s*(?:,\s*(?P<ou>O|U|Over|Under))?\s*$", re.I)
QUESTION_RE = re.compile(r"^\s*(?P<n>\d+)[.)]\s*(?P<text>.+)$")
ANSWER_RE = re.compile(r"^\s*(OVER|UNDER|O|U)\s*$", re.I)
LINE_RE = re.compile(r"over\s+or\s+under\s+(?P<line>\d+(?:\.\d+)?)\s*(?P<unit>[a-z ]*)", re.I)
TIEBREAK_RE = re.compile(r"combined\s+score\s*[-:]?\s*(?P<n>\d+)?", re.I)

STAT_WORDS = [
    ("sacks", ("sack",)),
    ("rush_yds", ("rush", "rushing", "on the ground")),
    ("pass_yds", ("passing yards", "pass for", "throw for", "passing")),
    ("total_yds", ("total yards", "yards of offense", "total offense")),
    ("turnovers", ("turnover",)),
    ("points", ("points", "score")),
]


def _num(s):
    s = s.replace(" ", "")
    return 0.0 if s.lower() in ("pk", "pick") else float(s)


def parse_card(text):
    """Return {"games": [...], "props": [...], "tiebreaker": int|None, "has_tiebreaker": bool}."""
    lines = [l.rstrip() for l in text.splitlines()]
    games, props = [], []
    section = "games"
    tiebreaker, has_tiebreaker = None, False
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        low = line.lower()
        if not line:
            i += 1
            continue
        if low.startswith("questions"):
            section = "props"
            i += 1
            continue
        if "mid-major" in low or "mid major" in low:
            section = "mid_major"
            i += 1
            continue
        tb = TIEBREAK_RE.search(line)
        if tb and low.startswith("combined"):
            has_tiebreaker = True
            tiebreaker = int(tb.group("n")) if tb.group("n") else None
            i += 1
            continue

        m = GAME_RE.match(line)
        if m:
            g = {"kind": "mid_major" if section == "mid_major" else "game",
                 "away": m.group("away").strip(), "home": m.group("home").strip(),
                 "home_spread": _num(m.group("spread")),
                 "total": float(m.group("total")) if m.group("total") else None,
                 "weight": int(m.group("weight")) if m.group("weight") else 1,
                 "my_ats_team": None, "my_ou": None}
            nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
            pm = PICK_RE.match(nxt) if nxt and not GAME_RE.match(nxt) and not QUESTION_RE.match(nxt) else None
            if pm and not nxt.lower().startswith(("questions", "mid", "combined")):
                g["my_ats_team"] = pm.group("team").strip()
                if pm.group("ou"):
                    g["my_ou"] = "over" if pm.group("ou").upper().startswith("O") else "under"
                i += 1
            games.append(g)
            i += 1
            continue

        q = QUESTION_RE.match(line)
        if q and section == "props":
            text_parts = [q.group("text").strip()]
            j = i + 1
            while j < len(lines) and lines[j].strip() and not QUESTION_RE.match(lines[j]) \
                    and not ANSWER_RE.match(lines[j]) and not lines[j].strip().lower().startswith(("mid", "combined")):
                text_parts.append(lines[j].strip())
                j += 1
            qtext = " ".join(text_parts)
            answer = None
            if j < len(lines) and ANSWER_RE.match(lines[j]):
                answer = "over" if lines[j].strip().upper().startswith("O") else "under"
                j += 1
            lm = LINE_RE.search(qtext)
            props.append({"kind": "prop", "n": int(q.group("n")), "question": qtext,
                          "line": float(lm.group("line")) if lm else None,
                          "unit": (lm.group("unit") or "").strip() if lm else "", "my_pick": answer})
            i = j
            continue
        i += 1
    return {"games": games, "props": props, "tiebreaker": tiebreaker, "has_tiebreaker": has_tiebreaker}


# ---------- matching ----------

def _key(name, ref):
    return teams_mod.norm(teams_mod.canonical(name, ref))


def match_team(name, candidates, ref=None):
    """Best match for a card team name among candidate team names (CFBD spellings)."""
    k = _key(name, ref)
    keys = {c: _key(c, ref) for c in candidates}
    for c, ck in keys.items():
        if ck == k:
            return c
    abbrev = k.replace(" st", " state").replace("st ", "state ")
    for c, ck in keys.items():
        if ck == abbrev or ck.startswith(k + " ") or (len(k) >= 4 and ck.startswith(k)):
            return c
    best = difflib.get_close_matches(k, list(keys.values()), n=1, cutoff=0.8)
    if best:
        return next(c for c, ck in keys.items() if ck == best[0])
    return None


def match_games(card_games, week_games, ref=None):
    """Attach game_id (and CFBD names) to each card game; also handles home/away listed reversed."""
    names = sorted({g["home_team"] for g in week_games} | {g["away_team"] for g in week_games})
    for cg in card_games:
        a, h = match_team(cg["away"], names, ref), match_team(cg["home"], names, ref)
        cg["game_id"], cg["flipped"] = None, False
        for g in week_games:
            if (g["away_team"], g["home_team"]) == (a, h):
                cg["game_id"] = g["id"]
            elif (g["away_team"], g["home_team"]) == (h, a):
                cg["game_id"], cg["flipped"] = g["id"], True
        cg["cfbd_away"], cg["cfbd_home"] = a, h
        if cg.get("my_ats_team"):
            pick = match_team(cg["my_ats_team"], [cg["away"], cg["home"]], ref) or \
                (cg["away"] if cg["my_ats_team"].lower()[:4] in cg["away"].lower() else
                 cg["home"] if cg["my_ats_team"].lower()[:4] in cg["home"].lower() else None)
            cg["my_ats"] = "home" if pick == cg["home"] else "away" if pick == cg["away"] else None
    return card_games


def interpret_prop(prop, week_games, ref=None):
    """Guess team / opponent / stat / scope from the question text."""
    q = prop["question"]
    low = q.lower()
    stat = next((s for s, words in STAT_WORDS if any(w in low for w in words)), None)
    if stat is None and "yard" in low:
        stat = "total_yds"
    combined = any(w in low for w in ("combine", "combined", "total between", "both teams"))
    if combined and stat in (None, "rush_yds", "pass_yds") and "total yards" in low:
        stat = "total_yds"
    found = []
    for g in week_games:
        for t in (g["home_team"], g["away_team"]):
            variants = {t, teams_mod.canonical(t, ref)} | set((ref or {}).get("aliases", {}).get(teams_mod.canonical(t, ref), []))
            pos = min((low.find(v.lower()) for v in variants if low.find(v.lower()) >= 0), default=-1)
            if pos >= 0:
                found.append((pos, t, g))
    found.sort(key=lambda x: x[0])
    team = found[0][1] if found else None
    game = found[0][2] if found else None
    opponent = None
    if game:
        opponent = game["away_team"] if team == game["home_team"] else game["home_team"]
    return {**prop, "team": team, "opponent": opponent, "game_id": game["id"] if game else None,
            "stat": stat, "scope": "combined" if combined else "team"}


# ---------- export + grading ----------

def _fmt_spread(s):
    return "PK" if s == 0 else f"{s:+g}"


def export_text(card, picks, props, tiebreaker):
    """Render picks in the pool email's format."""
    out = []
    for g in [g for g in card if g["kind"] == "game"]:
        line = f"{g['away']} @ {g['home']} ({_fmt_spread(g['home_spread'])})"
        if g["total"] is not None:
            line += f" o/u {g['total']:g}"
        if g["weight"] > 1:
            line += f" *{g['weight']} Point Game*"
        out.append(line)
        p = picks.get(g["idx"], {})
        team = g["home"] if p.get("ats") == "home" else g["away"] if p.get("ats") == "away" else "?"
        ou = {"over": "O", "under": "U"}.get(p.get("ou"), "?")
        out.append(f"{team}, {ou}" if g["total"] is not None else team)
    if props:
        out.append("Questions")
        for pr in props:
            out.append(f"{pr['n']}. {pr['question']}")
            out.append((pr.get("pick") or "?").upper())
    mids = [g for g in card if g["kind"] == "mid_major"]
    if mids:
        out.append("")
        out.append("Mid-Major Contest")
        for g in mids:
            out.append(f"{g['away']} @ {g['home']} ({_fmt_spread(g['home_spread'])})")
            p = picks.get(g["idx"], {})
            out.append((g["home"] if p.get("ats") == "home" else g["away"] if p.get("ats") == "away" else "?").upper())
    if tiebreaker is not None:
        out.append(f"Combined Score - {tiebreaker}")
    return "\n".join(out)


def grade_game_item(item, game):
    """Grade a card game against the POOL line. game: CFBD game (home/away as CFBD has them)."""
    if not game or game.get("home_points") is None or not game.get("completed"):
        return None, None
    hp, ap = game["home_points"], game["away_points"]
    if item.get("flipped"):
        hp, ap = ap, hp
    return (grade_ats(item.get("my_ats"), item["home_spread"], hp, ap),
            grade_ou(item.get("my_ou"), item["total"], hp, ap) if item.get("total") is not None else None)


def prop_actual(prop, box):
    """box: {(game_id, team): {...}} from backtest.collect.parse_box."""
    if not prop.get("stat") or not prop.get("team") or prop.get("game_id") is None:
        return None
    mine = box.get((prop["game_id"], prop["team"]))
    if not mine or mine.get(prop["stat"]) is None:
        return None
    if prop["scope"] == "combined":
        other = box.get((prop["game_id"], prop["opponent"]))
        if not other or other.get(prop["stat"]) is None:
            return None
        return mine[prop["stat"]] + other[prop["stat"]]
    return mine[prop["stat"]]


def grade_prop(pick, line, actual):
    if pick not in ("over", "under") or line is None or actual is None:
        return None
    if actual == line:
        return "P"
    return "W" if (actual > line) == (pick == "over") else "L"


_NICK = None


def _nicknames():
    global _NICK
    if _NICK is None:
        import json as _json
        import os as _os
        try:
            with open(_os.path.join(_os.path.dirname(__file__), "reference", "team_nicknames.json")) as f:
                _NICK = {k: v for k, v in _json.load(f).items() if not k.startswith("_")}
        except OSError:
            _NICK = {}
    return _NICK


def which_team(short, candidates, ref=None):
    """Resolve a pick like 'UNC', 'MISS ST', 'TENN', 'WESTERN MICH' to one of the two teams in a game."""
    hit = match_team(short, candidates, ref)
    if hit:
        return hit
    low = re.sub(r"[^a-z0-9& ]", "", short.lower()).strip()
    nick = _nicknames()
    for name in candidates:
        if low in nick.get(name, []) or low in nick.get(teams_mod.canonical(name, ref), []):
            return name
    for name in candidates:   # initials: "nc"/"unc" for North Carolina, "wmu" for Western Michigan
        words = [w for w in re.sub(r"[^a-z ]", "", name.lower()).split() if w not in ("of", "the")]
        initials = "".join(w[0] for w in words)
        if len(words) > 1 and low in (initials, "u" + initials, initials + "u"):
            return name
    for name in candidates:   # prefixes: "tenn" -> Tennessee, "miss st" -> Mississippi State, "western mich"
        words, parts = name.lower().split(), low.split()
        if parts and len(parts) <= len(words) and all(w.startswith(p) for w, p in zip(words, parts)):
            return name
    return None


def parse_entry(text, card_items, ref=None):
    """A league member's reply -> picks keyed by card item idx, plus their combined-score guess.

    card_items: pool items ({idx, kind, data}). Games are matched by team names (falling back to
    order on the card); questions by number."""
    parsed = parse_card(text)
    game_items = [it for it in card_items if it["kind"] in ("game", "mid_major")]
    prop_items = {it["data"].get("n"): it for it in card_items if it["kind"] == "prop"}
    picks, warnings = {}, []
    used = set()
    for pos, g in enumerate(parsed["games"]):
        target = None
        for it in game_items:
            d = it["data"]
            if it["idx"] in used:
                continue
            if match_team(g["away"], [d["away"]], ref) and match_team(g["home"], [d["home"]], ref):
                target = it
                break
        if target is None and pos < len(game_items) and game_items[pos]["idx"] not in used:
            target = game_items[pos]
            warnings.append(f"Matched '{g['away']} @ {g['home']}' by position on the card")
        if target is None:
            warnings.append(f"Couldn't match game '{g['away']} @ {g['home']}'")
            continue
        used.add(target["idx"])
        d = target["data"]
        side = None
        if g.get("my_ats_team"):
            t = g["my_ats_team"]
            hit = which_team(t, [d["away"], d["home"]], ref)
            side = "home" if hit == d["home"] else "away" if hit == d["away"] else None
            if side is None:
                warnings.append(f"Couldn't tell which team '{t}' is in {d['away']} @ {d['home']}")
        elif g.get("my_ou") is None:
            warnings.append(f"No pick found for {d['away']} @ {d['home']}")
        picks[str(target["idx"])] = {"ats": side, "ou": g.get("my_ou")}
    for pr in parsed["props"]:
        it = prop_items.get(pr["n"])
        if it is None:
            warnings.append(f"Question {pr['n']} isn't on this week's card")
            continue
        if pr.get("my_pick") is None:
            warnings.append(f"No answer found for question {pr['n']}")
        picks[str(it["idx"])] = {"pick": pr.get("my_pick")}
    missing = [it for it in game_items if it["idx"] not in used]
    for it in missing:
        warnings.append(f"No pick for {it['data']['away']} @ {it['data']['home']}")
    if parsed["has_tiebreaker"] and parsed["tiebreaker"] is None:
        warnings.append("No combined-score guess found")
    return {"picks": picks, "tiebreaker": parsed["tiebreaker"], "warnings": warnings}
