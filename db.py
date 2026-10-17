import json
import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.getenv("CFB_DB_PATH", os.path.join(os.path.dirname(__file__), "picks.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id              INTEGER PRIMARY KEY,      -- CFBD game id
    season          INTEGER NOT NULL,
    week            INTEGER NOT NULL,
    season_type     TEXT NOT NULL,
    start_date      TEXT,                     -- ISO 8601 UTC
    home_team       TEXT NOT NULL,
    away_team       TEXT NOT NULL,
    home_conf       TEXT,
    away_conf       TEXT,
    neutral_site    INTEGER DEFAULT 0,
    venue           TEXT,
    home_points     INTEGER,
    away_points     INTEGER,
    completed       INTEGER DEFAULT 0,
    spread          REAL,                     -- current line, home perspective
    total           REAL,
    spread_open     REAL,
    total_open      REAL,
    provider        TEXT,
    lines_updated_at TEXT,
    notes           TEXT DEFAULT '',
    venue_id        INTEGER,
    weather         TEXT,                     -- JSON forecast summary for the game window
    weather_updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_games_week ON games(season, season_type, week);

CREATE TABLE IF NOT EXISTS predictions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id         INTEGER NOT NULL REFERENCES games(id),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    model           TEXT NOT NULL,
    spread          REAL,                     -- line Claude predicted against (home perspective)
    total           REAL,
    notes           TEXT,
    ats_pick        TEXT NOT NULL,            -- 'home' | 'away'
    ou_pick         TEXT NOT NULL,            -- 'over' | 'under'
    proj_home       INTEGER,
    proj_away       INTEGER,
    confidence      INTEGER,
    reasoning       TEXT,
    key_factors     TEXT,                     -- JSON array
    locked          INTEGER DEFAULT 0,
    ats_result      TEXT,                     -- graded against this row's own line
    ou_result       TEXT,
    conditions_impact TEXT,
    context         TEXT                      -- JSON snapshot: profiles, weather assessment, injuries
);
CREATE INDEX IF NOT EXISTS idx_pred_game ON predictions(game_id);

CREATE TABLE IF NOT EXISTS team_profiles (
    season          INTEGER NOT NULL,
    team            TEXT NOT NULL,
    data            TEXT NOT NULL,            -- JSON from analytics.build_profiles
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (season, team)
);

CREATE TABLE IF NOT EXISTS teams (
    season          INTEGER NOT NULL,
    school          TEXT NOT NULL,
    conference      TEXT,
    data            TEXT NOT NULL,            -- JSON from teams.parse_teams
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (season, school)
);

CREATE TABLE IF NOT EXISTS venues (
    id              INTEGER PRIMARY KEY,
    name            TEXT,
    latitude        REAL,
    longitude       REAL,
    elevation       REAL,
    dome            INTEGER DEFAULT 0,
    grass           INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS injuries (
    game_id         INTEGER PRIMARY KEY REFERENCES games(id),
    report          TEXT NOT NULL,
    sources         TEXT,                     -- JSON [{url, title}]
    fetched_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS intel (
    team            TEXT NOT NULL,
    season          INTEGER NOT NULL,
    cutoff_key      TEXT NOT NULL,            -- 'live' or the cutoff ISO time for replays
    data            TEXT NOT NULL,
    fetched_at      TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (team, season, cutoff_key)
);

CREATE TABLE IF NOT EXISTS pool_cards (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    season          INTEGER NOT NULL,
    week            INTEGER NOT NULL,
    season_type     TEXT NOT NULL DEFAULT 'regular',
    raw             TEXT NOT NULL,
    has_tiebreaker  INTEGER DEFAULT 0,
    tiebreaker      INTEGER,                  -- my combined-score guess
    deadline        TEXT,                     -- ISO UTC; picks lock here (default Friday 12:00 PM local)
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (season, week, season_type)
);

CREATE TABLE IF NOT EXISTS pool_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id         INTEGER NOT NULL REFERENCES pool_cards(id) ON DELETE CASCADE,
    idx             INTEGER NOT NULL,         -- order on the card
    kind            TEXT NOT NULL,            -- game | mid_major | prop
    data            TEXT NOT NULL,            -- JSON: pool line, teams, matched game, prop interpretation
    my_ats          TEXT,                     -- home | away (card's home/away)
    my_ou           TEXT,                     -- over | under
    my_prop         TEXT,                     -- over | under
    claude          TEXT,                     -- JSON prediction against the POOL line
    ats_result      TEXT,
    ou_result       TEXT,
    prop_result     TEXT,
    prop_actual     REAL,
    claude_ats_result TEXT,
    claude_ou_result  TEXT,
    claude_prop_result TEXT
);

CREATE TABLE IF NOT EXISTS reviews (
    card_id         INTEGER PRIMARY KEY REFERENCES pool_cards(id) ON DELETE CASCADE,
    data            TEXT NOT NULL,            -- JSON: stats + Claude's item verdicts + summary
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS lessons (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id         INTEGER REFERENCES pool_cards(id) ON DELETE CASCADE,
    season          INTEGER NOT NULL,
    week            INTEGER NOT NULL,
    season_type     TEXT NOT NULL DEFAULT 'regular',
    text            TEXT NOT NULL,
    applies_to      TEXT,
    evidence        TEXT,
    active          INTEGER DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS players (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS entries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id         INTEGER NOT NULL REFERENCES pool_cards(id) ON DELETE CASCADE,
    player_id       INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    raw             TEXT NOT NULL,
    picks           TEXT NOT NULL,            -- JSON {item idx: {ats, ou} | {pick}}
    tiebreaker      INTEGER,
    reported_points REAL,                     -- the league's posted score, used to infer unknown rules
    warnings        TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (card_id, player_id)
);

CREATE TABLE IF NOT EXISTS settings (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS picks (
    game_id         INTEGER PRIMARY KEY REFERENCES games(id),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    prediction_id   INTEGER REFERENCES predictions(id),  -- Claude pick locked with mine
    ats_pick        TEXT,                     -- 'home' | 'away' | NULL
    ou_pick         TEXT,                     -- 'over' | 'under' | NULL
    spread          REAL,                     -- line I picked against (home perspective)
    total           REAL,
    ats_result      TEXT,
    ou_result       TEXT,
    graded_at       TEXT
);
"""


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def tx():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# Columns added after the first release; ALTERed into older databases.
MIGRATIONS = {
    "games": {"venue_id": "INTEGER", "weather": "TEXT", "weather_updated_at": "TEXT"},
    "predictions": {"conditions_impact": "TEXT", "context": "TEXT"},
    "pool_cards": {"deadline": "TEXT"},
}


def init_db():
    with tx() as conn:
        for table, cols in MIGRATIONS.items():
            existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
            if existing:
                for col, ddl in cols.items():
                    if col not in existing:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        conn.executescript(SCHEMA)


def row_to_dict(row):
    if row is None:
        return None
    d = dict(row)
    for key in ("key_factors", "weather", "context", "sources"):
        if d.get(key):
            d[key] = json.loads(d[key])
    return d


def upsert_game(conn, g):
    """Insert or update a game, preserving notes. Only overwrites line fields when a line is given."""
    conn.execute(
        """
        INSERT INTO games (id, season, week, season_type, start_date, home_team, away_team,
                           home_conf, away_conf, neutral_site, venue, venue_id, home_points, away_points, completed)
        VALUES (:id, :season, :week, :season_type, :start_date, :home_team, :away_team,
                :home_conf, :away_conf, :neutral_site, :venue, :venue_id, :home_points, :away_points, :completed)
        ON CONFLICT(id) DO UPDATE SET
            week=excluded.week, start_date=excluded.start_date,
            home_conf=excluded.home_conf, away_conf=excluded.away_conf,
            neutral_site=excluded.neutral_site, venue=excluded.venue,
            venue_id=COALESCE(excluded.venue_id, games.venue_id),
            home_points=excluded.home_points, away_points=excluded.away_points,
            completed=excluded.completed
        """,
        {"venue_id": None, **g},
    )


def update_line(conn, game_id, line):
    conn.execute(
        """
        UPDATE games SET spread=:spread, total=:total, spread_open=:spread_open,
               total_open=:total_open, provider=:provider, lines_updated_at=datetime('now')
        WHERE id=:id
        """,
        {**line, "id": game_id},
    )


def save_profiles(conn, season, profiles):
    conn.executemany(
        """INSERT INTO team_profiles (season, team, data, updated_at) VALUES (?, ?, ?, datetime('now'))
           ON CONFLICT(season, team) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at""",
        [(season, team, json.dumps(p)) for team, p in profiles.items()],
    )


def get_profile(conn, season, team):
    row = conn.execute("SELECT data FROM team_profiles WHERE season=? AND team=?", (season, team)).fetchone()
    return json.loads(row["data"]) if row else None


def profiles_age_hours(conn, season):
    row = conn.execute(
        "SELECT (julianday('now') - julianday(MAX(updated_at))) * 24 AS h FROM team_profiles WHERE season=?",
        (season,)).fetchone()
    return row["h"] if row and row["h"] is not None else None


def save_venues(conn, venues):
    conn.executemany(
        """INSERT INTO venues (id, name, latitude, longitude, elevation, dome, grass)
           VALUES (:id, :name, :latitude, :longitude, :elevation, :dome, :grass)
           ON CONFLICT(id) DO UPDATE SET name=excluded.name, latitude=excluded.latitude,
               longitude=excluded.longitude, elevation=excluded.elevation, dome=excluded.dome, grass=excluded.grass""",
        list(venues.values()),
    )


def save_teams(conn, season, teams):
    conn.execute("DELETE FROM teams WHERE season=?", (season,))
    conn.executemany("INSERT INTO teams (season, school, conference, data) VALUES (?, ?, ?, ?)",
                     [(season, t["school"], t.get("conference"), json.dumps(t)) for t in teams])


def get_teams(conn, season):
    return [json.loads(r["data"]) for r in conn.execute("SELECT data FROM teams WHERE season=? ORDER BY school", (season,))]


def teams_age_hours(conn, season):
    row = conn.execute("SELECT (julianday('now') - julianday(MAX(updated_at))) * 24 AS h FROM teams WHERE season=?",
                       (season,)).fetchone()
    return row["h"] if row and row["h"] is not None else None
