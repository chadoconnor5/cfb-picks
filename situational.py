"""Situational factors for one game, from the history warehouse (no API calls).

Travel distance, time zones crossed, body-clock kickoff time, altitude, surface, rest (bye / short
week), rivalry, conference game, revenge, look-ahead / letdown spots and bowl-eligibility stakes.
Everything respects `as_of`: only games and polls from before that moment are used.
"""
import json
import math
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

REF = os.path.join(os.path.dirname(__file__), "reference", "rivalries.json")
M_TO_FT = 3.28084  # CFBD venue elevation is in meters
HIGH_ALTITUDE_M = 1500


def _parse(s):
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def haversine_miles(a, b):
    if None in (a[0], a[1], b[0], b[1]):
        return None
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return round(3958.8 * 2 * math.asin(math.sqrt(h)))


def load_rivalries():
    try:
        with open(REF, encoding="utf-8") as f:
            return {frozenset((a, b)): name for a, b, name in json.load(f)["rivalries"]}
    except OSError:
        return {}


def rivalry_name(a, b):
    return load_rivalries().get(frozenset((a, b)))


def _team_row(conn, team, season):
    return conn.execute("SELECT * FROM bt_teams WHERE school=? AND season<=? ORDER BY season DESC LIMIT 1",
                        (team, season)).fetchone()


def _venue(conn, venue_id):
    return conn.execute("SELECT * FROM bt_venues WHERE id=?", (venue_id,)).fetchone() if venue_id else None


def _offset_hours(tz, when):
    try:
        return ZoneInfo(tz).utcoffset(when.replace(tzinfo=None)).total_seconds() / 3600
    except Exception:
        return None


def _schedule(conn, team, season):
    rows = conn.execute("""SELECT * FROM bt_games WHERE season=? AND (home_team=? OR away_team=?) AND start_date IS NOT NULL
                           ORDER BY start_date""", (season, team, team)).fetchall()
    return [dict(r) for r in rows]


def _ranked(conn, season, team, week, season_type="regular"):
    """AP rank in the poll used for this week (CFBD poll week N = the poll released before week N games)."""
    if season_type == "regular":
        row = conn.execute("""SELECT rank FROM bt_rankings WHERE season=? AND team=? AND poll LIKE 'AP%'
                              AND season_type='regular' AND week<=? ORDER BY week DESC LIMIT 1""",
                           (season, team, week)).fetchone()
    else:
        row = conn.execute("""SELECT rank FROM bt_rankings WHERE season=? AND team=? AND poll LIKE 'AP%'
                              AND season_type='regular' ORDER BY week DESC LIMIT 1""", (season, team)).fetchone()
    return row["rank"] if row else None


def team_situation(conn, team, opp, game, as_of):
    season = game["season"]
    kick = _parse(game["start_date"]) if game.get("start_date") else None
    sched = _schedule(conn, team, season)
    past = [g for g in sched if kick and _parse(g["start_date"]) < kick and _parse(g["start_date"]) < as_of
            and g["home_points"] is not None]
    future = [g for g in sched if kick and _parse(g["start_date"]) > kick]
    out = {"team": team}

    # Rest
    if past and kick:
        last = past[-1]
        days = (kick - _parse(last["start_date"])).total_seconds() / 86400
        out["rest_days"] = round(days, 1)
        out["off_bye"] = days >= 12.5
        out["short_week"] = days <= 5.5
        last_opp = last["away_team"] if last["home_team"] == team else last["home_team"]
        tp = last["home_points"] if last["home_team"] == team else last["away_points"]
        op = last["away_points"] if last["home_team"] == team else last["home_points"]
        out["last_game"] = {"opponent": last_opp, "score": f"{tp}-{op}", "won": tp > op,
                            "rival": rivalry_name(team, last_opp),
                            "opp_rank": _ranked(conn, season, last_opp, last["week"], last["season_type"])}

    # Look-ahead
    if future:
        nxt = future[0]
        nxt_opp = nxt["away_team"] if nxt["home_team"] == team else nxt["home_team"]
        out["next_game"] = {"opponent": nxt_opp, "date": nxt["start_date"][:10],
                            "rival": rivalry_name(team, nxt_opp),
                            "opp_rank": _ranked(conn, season, nxt_opp, game["week"], game.get("season_type", "regular"))}

    # Record + bowl stakes
    w = sum(1 for g in past if (g["home_points"] > g["away_points"]) == (g["home_team"] == team) and g["home_points"] != g["away_points"])
    l = len(past) - w
    out["record"] = f"{w}-{l}"
    remaining = len([g for g in future if g["season_type"] == "regular"]) + 1  # include this game
    if w < 6 and game.get("season_type", "regular") == "regular":
        need = 6 - w
        out["bowl_stakes"] = f"needs {need} more win{'s' if need != 1 else ''} in {remaining} remaining games for bowl eligibility" \
            if need <= remaining else "can no longer reach 6 wins"

    # Revenge: most recent previous meeting
    prev = conn.execute("""SELECT * FROM bt_games WHERE home_points IS NOT NULL AND start_date < ?
                           AND ((home_team=? AND away_team=?) OR (home_team=? AND away_team=?))
                           ORDER BY start_date DESC LIMIT 1""",
                        ((kick or as_of).isoformat(), team, opp, opp, team)).fetchone()
    if prev:
        tp = prev["home_points"] if prev["home_team"] == team else prev["away_points"]
        op = prev["away_points"] if prev["home_team"] == team else prev["home_points"]
        out["last_meeting"] = {"season": prev["season"], "score": f"{tp}-{op}", "won": tp > op,
                               "postseason": prev["season_type"] == "postseason"}
    out["rank"] = _ranked(conn, season, team, game["week"], game.get("season_type", "regular"))
    return out


def game_situation(conn, game, as_of=None):
    """game: dict with season, season_type, start_date, home_team, away_team, neutral_site, venue_id."""
    if conn is None:
        return None
    as_of = as_of or datetime.now(timezone.utc)
    season = game["season"]
    home, away = game["home_team"], game["away_team"]
    kick = _parse(game["start_date"]) if game.get("start_date") else None
    venue = _venue(conn, game.get("venue_id"))
    home_row, away_row = _team_row(conn, home, season), _team_row(conn, away, season)
    v_lat = venue["latitude"] if venue else (home_row["latitude"] if home_row else None)
    v_lon = venue["longitude"] if venue else (home_row["longitude"] if home_row else None)
    v_tz = (venue["timezone"] if venue and venue["timezone"] else None) or (home_row["timezone"] if home_row else None)

    out = {"season": season, "site": "neutral" if game.get("neutral_site") else "home",
           "venue": venue["name"] if venue else None,
           "rivalry": rivalry_name(home, away),
           "conference_game": bool(game.get("conference_game")) or bool(
               home_row and away_row and home_row["conference"] and home_row["conference"] == away_row["conference"]),
           "teams": {}}
    if venue and venue["elevation"] is not None:
        out["elevation_ft"] = round(venue["elevation"] * M_TO_FT)
    if venue is not None and venue["grass"] is not None:
        out["surface"] = "grass" if venue["grass"] else "artificial turf"

    for side, team, opp, row in (("home", home, away, home_row), ("away", away, home, away_row)):
        t = team_situation(conn, team, opp, game, as_of)
        if row and row["latitude"] is not None:
            t["travel_miles"] = 0 if (side == "home" and not game.get("neutral_site")) else haversine_miles(
                (row["latitude"], row["longitude"]), (v_lat, v_lon))
        if kick and row and row["timezone"] and v_tz:
            o_team, o_site = _offset_hours(row["timezone"], kick), _offset_hours(v_tz, kick)
            if o_team is not None and o_site is not None:
                t["time_zones_crossed"] = int(o_site - o_team)  # + = traveled east
                local = kick.astimezone(ZoneInfo(row["timezone"]))
                t["body_clock_kickoff"] = local.strftime("%-I:%M %p") + " home time"
                t["early_body_clock"] = local.hour < 11
        if row:
            hv = _venue(conn, row["venue_id"])
            if hv is not None and venue is not None and hv["elevation"] is not None and venue["elevation"] is not None:
                t["altitude_jump_ft"] = round((venue["elevation"] - hv["elevation"]) * M_TO_FT)
            if hv is not None and venue is not None and hv["grass"] is not None and venue["grass"] is not None:
                t["surface_change"] = bool(hv["grass"]) != bool(venue["grass"])
        out["teams"][side] = t
    return out


def flags(s):
    """Short human-readable flags worth Claude's attention."""
    if not s:
        return []
    f = []
    if s.get("rivalry"):
        f.append(f"Rivalry game: {s['rivalry']}")
    if s.get("elevation_ft", 0) >= HIGH_ALTITUDE_M * M_TO_FT:
        f.append(f"High altitude: {s['elevation_ft']:,} ft")
    for t in s["teams"].values():
        name = t["team"]
        if t.get("off_bye"):
            f.append(f"{name} is coming off a bye ({t['rest_days']} days rest)")
        if t.get("short_week"):
            f.append(f"{name} is on a short week ({t['rest_days']} days rest)")
        if (t.get("travel_miles") or 0) >= 1000:
            f.append(f"{name} travels {t['travel_miles']:,} miles")
        if abs(t.get("time_zones_crossed") or 0) >= 2:
            f.append(f"{name} crosses {abs(t['time_zones_crossed'])} time zones "
                     f"({'east' if t['time_zones_crossed'] > 0 else 'west'}); kickoff {t.get('body_clock_kickoff')}")
        elif t.get("early_body_clock"):
            f.append(f"{name} kicks off at {t.get('body_clock_kickoff')}")
        if (t.get("altitude_jump_ft") or 0) >= 3000:
            f.append(f"{name} plays {t['altitude_jump_ft']:,} ft higher than at home")
        lm = t.get("last_meeting")
        if lm and not lm["won"] and lm["season"] >= s.get("season", 0) - 1:
            f.append(f"Revenge spot: {name} lost the last meeting ({lm['score']}, {lm['season']}"
                     f"{', postseason' if lm['postseason'] else ''})")
        ng = t.get("next_game")
        if ng and (ng.get("rival") or (ng.get("opp_rank") and ng["opp_rank"] <= 15)):
            tag = " (rival)" if ng.get("rival") else f" (No. {ng['opp_rank']})"
            f.append(f"Look-ahead risk: {name} plays {ng['opponent']}{tag} next")
        lg = t.get("last_game")
        if lg and lg["won"] and (lg.get("rival") or (lg.get("opp_rank") and lg["opp_rank"] <= 10)):
            f.append(f"Letdown risk: {name} is coming off a big win over {lg['opponent']} ({lg['score']})")
        if t.get("bowl_stakes"):
            f.append(f"{name} ({t['record']}) {t['bowl_stakes']}")
    return f


def describe(s):
    if not s:
        return "  (no situational data; run python -m backtest.collect)"
    lines = []
    for side in ("away", "home"):
        t = s["teams"][side]
        bits = [f"record {t.get('record')}"]
        if t.get("rank"):
            bits.append(f"AP No. {t['rank']}")
        if t.get("rest_days") is not None:
            bits.append(f"{t['rest_days']} days rest")
        if t.get("travel_miles") is not None:
            bits.append(f"travel {t['travel_miles']:,} mi")
        if t.get("time_zones_crossed"):
            bits.append(f"{t['time_zones_crossed']:+d} time zones")
        if t.get("body_clock_kickoff"):
            bits.append(f"kickoff {t['body_clock_kickoff']}")
        lines.append(f"  {t['team']}: " + ", ".join(bits))
    meta = [s["site"] + " site"]
    if s.get("venue"):
        meta.append(s["venue"])
    if s.get("surface"):
        meta.append(s["surface"])
    if s.get("elevation_ft") is not None:
        meta.append(f"elevation {s['elevation_ft']:,} ft")
    if s.get("conference_game"):
        meta.append("conference game")
    lines.insert(0, "  " + ", ".join(meta))
    lines += [f"  - {x}" for x in flags(s)]
    return "\n".join(lines)
