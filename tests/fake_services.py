"""A fake CollegeFootballData + Open-Meteo server for testing build_site.py and the website offline.

    python tests/fake_services.py 8765
    CFBD_BASE_URL=http://127.0.0.1:8765 OPEN_METEO_URL=http://127.0.0.1:8765/v1/forecast \
    OPEN_METEO_PREVIOUS_RUNS_URL=http://127.0.0.1:8765/v1/forecast OPEN_METEO_ARCHIVE_URL=http://127.0.0.1:8765/v1/archive \
    CFBD_API_KEY=x python build_site.py --weeks 7

Season 2026: weeks 1-6 are final, week 7 is the user's real card (kickoff Sat Oct 17), week 8 is upcoming.
Pass FAKE_NOW (ISO) to the server to move "today".
"""
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(__file__))
from fixtures import adv  # noqa: E402

WEEK7 = [("Ohio State", "Indiana"), ("Mississippi State", "LSU"), ("Alabama", "Tennessee"), ("Missouri", "Ole Miss"),
         ("North Carolina", "Duke"), ("Notre Dame", "BYU"), ("Wisconsin", "UCLA"), ("Michigan State", "Northwestern"),
         ("Texas Tech", "Arizona State"), ("Washington State", "Oregon State"), ("Western Michigan", "Central Michigan")]
WEEK1 = [("Colorado", "Georgia Tech"), ("Baylor", "Auburn"), ("Tulane", "Duke"), ("Clemson", "LSU"), ("UCLA", "California"),
         ("Louisville", "Ole Miss"), ("SMU", "Florida State"), ("Liberty", "James Madison")]
NEUTRAL = {("Baylor", "Auburn"), ("Louisville", "Ole Miss")}
TEAMS = sorted({t for m in WEEK7 + WEEK1 for t in m} | {"North Dakota State", "Sacramento State"})
CONF = {"Ohio State": "Big Ten", "Indiana": "Big Ten", "Wisconsin": "Big Ten", "UCLA": "Big Ten", "Michigan State": "Big Ten",
        "Northwestern": "Big Ten", "Mississippi State": "SEC", "LSU": "SEC", "Alabama": "SEC", "Tennessee": "SEC",
        "Missouri": "SEC", "Ole Miss": "SEC", "North Carolina": "ACC", "Duke": "ACC", "Notre Dame": "FBS Independents",
        "BYU": "Big 12", "Texas Tech": "Big 12", "Arizona State": "Big 12", "Washington State": "Pac-12",
        "Oregon State": "Pac-12", "Western Michigan": "Mid-American", "Central Michigan": "Mid-American",
        "North Dakota State": "FBS Independents", "Sacramento State": "FBS Independents", "Colorado": "Big 12",
        "Georgia Tech": "ACC", "Baylor": "Big 12", "Auburn": "SEC", "Tulane": "American", "Clemson": "ACC", "California": "ACC",
        "Louisville": "ACC", "SMU": "ACC", "Florida State": "ACC", "Liberty": "Conference USA", "James Madison": "Sun Belt"}
VENUE = {t: 1000 + i for i, t in enumerate(TEAMS)}
LOC = {t: (30 + (i * 1.3) % 18, -120 + (i * 2.7) % 45) for i, t in enumerate(TEAMS)}
INDOOR = {"North Dakota State"}
KICK1 = datetime(2026, 9, 5, 19, 30, tzinfo=timezone.utc)


def h(*parts):
    return int(hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def schedule():
    games = []
    gid = 401000000
    for week in range(1, 9):
        if week == 7:
            pairs = list(WEEK7)
        elif week == 1:
            pairs = list(WEEK1)
        else:
            order = sorted(TEAMS, key=lambda t: h(week, t))
            pairs = [(order[i], order[i + 1]) for i in range(0, len(order) - 1, 2)]
        kick = KICK1 + timedelta(days=7 * (week - 1))
        for n, (a, home) in enumerate(pairs):
            gid += 1
            spread = -((h(gid, "s") % 29) - 14) / 2 or -3.5
            total = 41.5 + (h(gid, "t") % 24)
            final = week <= 6
            hp = 14 + h(gid, "hp") % 31 if final else None
            ap = 10 + h(gid, "ap") % 31 if final else None
            games.append({"id": gid, "season": 2026, "week": week, "seasonType": "regular",
                          "startDate": (kick + timedelta(hours=(n % 4) * 3.5)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                          "startTimeTBD": False, "completed": final, "neutralSite": (a, home) in NEUTRAL, "conferenceGame": CONF[a] == CONF[home],
                          "attendance": 50000 + h(gid) % 40000 if final else None, "venueId": VENUE[home], "venue": f"{home} Stadium",
                          "homeTeam": home, "homeConference": CONF[home], "homePoints": hp,
                          "awayTeam": a, "awayConference": CONF[a], "awayPoints": ap,
                          "_spread": spread, "_total": total})
    return games


GAMES = schedule()


def lines():
    out = []
    for g in GAMES:
        s, t = g["_spread"], g["_total"]
        out.append({"id": g["id"], "homeTeam": g["homeTeam"], "awayTeam": g["awayTeam"], "lines": [
            {"provider": "DraftKings", "spread": s, "spreadOpen": s + 1, "overUnder": t, "overUnderOpen": t - 1.5},
            {"provider": "ESPN Bet", "spread": s - 0.5, "spreadOpen": s + 0.5, "overUnder": t + 0.5, "overUnderOpen": t - 1}]})
    return out


def box(week):
    out = []
    for g in GAMES:
        if g["week"] != week or not g["completed"]:
            continue
        teams = []
        for side in ("home", "away"):
            t = g[f"{side}Team"]
            k = h(g["id"], t)
            stats = [{"category": "rushingYards", "stat": str(80 + k % 180)}, {"category": "netPassingYards", "stat": str(140 + k % 220)},
                     {"category": "totalYards", "stat": str(260 + k % 300)}, {"category": "sacks", "stat": str(k % 6)},
                     {"category": "turnovers", "stat": str(k % 4)}, {"category": "rushingAttempts", "stat": str(25 + k % 20)},
                     {"category": "completionAttempts", "stat": f"{15 + k % 10}-{25 + k % 15}"}, {"category": "tacklesForLoss", "stat": str(3 + k % 6)}]
            teams.append({"team": t, "homeAway": side, "points": g[f"{side}Points"], "stats": stats})
        out.append({"id": g["id"], "teams": teams})
    return out


def advanced_games(week):
    out = []
    for g in GAMES:
        if g["week"] != week or not g["completed"]:
            continue
        for side, other in (("home", "away"), ("away", "home")):
            k = h(g["id"], side)
            out.append({"gameId": g["id"], "team": g[f"{side}Team"], "opponent": g[f"{other}Team"],
                        "offense": {"ppa": (k % 60 - 20) / 100, "successRate": 0.35 + (k % 20) / 100, "explosiveness": 1.1,
                                    "passingPlays": {"ppa": (k % 70 - 20) / 100, "successRate": 0.4, "explosiveness": 1.4},
                                    "rushingPlays": {"ppa": (k % 50 - 20) / 100, "successRate": 0.42, "explosiveness": 1.0}},
                        "defense": {"ppa": (k % 40) / 100, "passingPlays": {"ppa": 0.2}, "rushingPlays": {"ppa": 0.1}}})
    return out


def season_adv():
    return [{**adv(t, 0.35 + (h(t) % 30) / 100, (h(t, 1) % 50) / 100, (h(t, 2) % 35) / 100), "team": t, "conference": CONF[t]}
            for t in TEAMS]


def hourly(q):
    p = parse_qs(q)
    start = datetime.fromisoformat(p["start_date"][0])
    end = datetime.fromisoformat(p.get("end_date", p["start_date"])[0]) + timedelta(days=1)
    times = []
    while start < end:
        times.append(start.strftime("%Y-%m-%dT%H:%M"))
        start += timedelta(hours=1)
    n = len(times)
    names = p.get("hourly", ["temperature_2m"])[0].split(",")
    base = {"temperature": 52, "wind_speed": 17, "wind_gusts": 28, "precipitation_probability": 70, "precipitation": 0.08,
            "snowfall": 0, "weather_code": 61}
    out = {"time": times}
    for name in names:
        v = next((val for k, val in base.items() if name.startswith(k)), 0)
        out[name] = [v] * n
    return {"hourly": out}


def now():
    return datetime.fromisoformat(os.getenv("FAKE_NOW", "2026-10-17T20:00:00+00:00"))


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        path = u.path
        if path.startswith("/v1/"):
            return self.send(hourly(u.query))
        year = int(q.get("year", 2026))
        if year != 2026 and path not in ("/venues",):
            return self.send([])
        if path == "/games":
            st = q.get("seasonType", "regular")
            gs = [g for g in GAMES if st == "regular" and ("week" not in q or g["week"] == int(q["week"]))]
            return self.send([{k: v for k, v in g.items() if not k.startswith("_")} for g in gs])
        if path == "/lines":
            return self.send(lines() if q.get("seasonType", "regular") == "regular" else [])
        if path == "/games/teams":
            return self.send(box(int(q.get("week", 0))))
        if path == "/stats/game/advanced":
            return self.send(advanced_games(int(q.get("week", 0))))
        if path == "/calendar":
            return self.send([{"season": 2026, "week": w, "seasonType": "regular",
                               "startDate": (KICK1 - timedelta(days=4) + timedelta(days=7 * (w - 1))).isoformat().replace("+00:00", "Z"),
                               "endDate": (KICK1 + timedelta(days=3) + timedelta(days=7 * (w - 1))).isoformat().replace("+00:00", "Z")}
                              for w in range(1, 15)])
        if path == "/venues":
            return self.send([{"id": VENUE[t], "name": f"{t} Stadium", "latitude": LOC[t][0], "longitude": LOC[t][1],
                               "dome": t in INDOOR, "grass": True, "capacity": 60000 + h(t) % 40000, "elevation": 200,
                               "timezone": "America/Chicago"} for t in TEAMS])
        if path in ("/teams", "/teams/fbs"):
            return self.send([{"school": t, "conference": CONF[t], "classification": "fbs",
                               "location": {"latitude": LOC[t][0], "longitude": LOC[t][1], "timezone": "America/Chicago",
                                            "venueId": VENUE[t], "name": f"{t} Stadium", "dome": t in INDOOR}} for t in TEAMS])
        if path == "/stats/season/advanced":
            return self.send(season_adv())
        if path == "/ratings/sp":
            return self.send([{"team": t, "rating": (h(t, "sp") % 400 - 150) / 10, "ranking": i + 1,
                               "offense": {"rating": 30}, "defense": {"rating": 22}} for i, t in enumerate(TEAMS)])
        if path == "/stats/season":
            return self.send([{"team": t, "statName": "games", "statValue": 6} for t in TEAMS])
        if path == "/talent":
            return self.send([{"team": t, "talent": 600 + h(t, "tal") % 400} for t in TEAMS])
        if path == "/coaches":
            return self.send([{"firstName": "Coach", "lastName": t.split()[0], "seasons": [
                {"school": t, "year": 2026, "games": 6, "wins": 3 + h(t) % 4, "losses": 2}]} for t in TEAMS])
        if path == "/rankings":
            return self.send([])
        return self.send([])


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
