"""FBS team list: pulled live from CFBD each season and checked against a verified reference.

Neither source is trusted blindly. reference/fbs_<season>.json is a hand-verified list (with
sources); if CFBD disagrees, the sync reports exactly which teams or conferences differ.
"""
import json
import os
import re

from cfbd import _f, _get, _num

REF_DIR = os.path.join(os.path.dirname(__file__), "reference")

CONF_ALIASES = {
    "mac": "mid-american", "mid american": "mid-american", "american athletic": "american", "aac": "american",
    "american conference": "american", "independent": "fbs independents", "independents": "fbs independents",
    "fbs independent": "fbs independents", "cusa": "conference usa", "c-usa": "conference usa",
    "mwc": "mountain west", "pac 12": "pac-12", "big 12": "big 12", "big ten": "big ten",
}


def load_reference(season):
    path = os.path.join(REF_DIR, f"fbs_{season}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_overrides():
    path = os.path.join(REF_DIR, "venue_overrides.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        return {}


def norm(name):
    s = (name or "").lower().replace("é", "e").replace("'", "").replace("’", "")
    s = re.sub(r"[^a-z0-9()& ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_conf(name):
    n = norm(name).replace("conference", "").strip() if name else ""
    n = n or norm(name)
    return CONF_ALIASES.get(n, CONF_ALIASES.get(norm(name), n))


def alias_map(ref):
    """normalized name -> canonical reference name."""
    out = {}
    for conf, teams in ref["conferences"].items():
        for t in teams:
            out[norm(t)] = t
    for canon, alts in ref.get("aliases", {}).items():
        for a in alts:
            out[norm(a)] = canon
    return out


def canonical(name, ref):
    if not ref:
        return name
    return alias_map(ref).get(norm(name), name)


def parse_teams(raw):
    out = []
    for t in raw:
        loc = t.get("location") or {}
        out.append({
            "school": t.get("school"),
            "conference": t.get("conference"),
            "abbreviation": t.get("abbreviation"),
            "classification": t.get("classification"),
            "latitude": _num(loc.get("latitude")),
            "longitude": _num(loc.get("longitude")),
            "timezone": _f(loc, "timezone"),
            "venue_id": _f(loc, "venueId", "venue_id", "id"),
            "venue": _f(loc, "name"),
            "elevation": _num(loc.get("elevation")),
            "capacity": _num(loc.get("capacity")),
            "grass": loc.get("grass"),
            "dome": loc.get("dome"),
        })
    return [t for t in out if t["school"]]


def fetch_fbs_teams(season):
    return parse_teams(_get("/teams/fbs", year=season))


def verify(teams, ref):
    """Compare CFBD's list with the reference. Returns a report dict."""
    if not ref:
        return {"checked": False, "count": len(teams), "message": "No verified reference for this season."}
    amap = alias_map(ref)
    ref_conf = {t: c for c, ts in ref["conferences"].items() for t in ts}
    seen, unexpected, conf_mismatch = set(), [], []
    for t in teams:
        canon = amap.get(norm(t["school"]))
        if not canon:
            unexpected.append(t["school"])
            continue
        seen.add(canon)
        if t.get("conference") and norm_conf(t["conference"]) != norm_conf(ref_conf[canon]):
            conf_mismatch.append({"team": canon, "cfbd": t["conference"], "reference": ref_conf[canon]})
    missing = sorted(set(ref_conf) - seen)
    ok = not missing and not unexpected and not conf_mismatch and len(teams) == ref["total"]
    return {"checked": True, "ok": ok, "count": len(teams), "expected": ref["total"], "missing": missing,
            "unexpected": sorted(unexpected), "conference_mismatches": conf_mismatch,
            "message": (f"All {ref['total']} FBS teams match the verified {ref['season']} list." if ok else
                        f"CFBD lists {len(teams)} FBS teams; the verified list has {ref['total']}. See differences.")}


def program_note(team, season):
    ref = load_reference(season)
    if not ref:
        return None
    return ref.get("program_notes", {}).get(canonical(team, ref))


def home_venue_override(team):
    """Known home-stadium facts that beat the API (e.g. indoor stadiums)."""
    return load_overrides().get(team)


def by_conference(teams, ref):
    groups = {}
    for t in teams:
        groups.setdefault(t.get("conference") or "Unknown", []).append(t)
    for v in groups.values():
        v.sort(key=lambda t: t["school"])
    return dict(sorted(groups.items()))
