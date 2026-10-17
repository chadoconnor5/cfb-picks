# CFB Picks

College football picks for a weekly pick'em pool: import the pool's card, get Claude's picks against the
pool's lines, make yours, and everything grades itself. It also keeps a league leaderboard from everyone's
picks, and Claude reviews its own misses each week so the next week's picks improve.

**Website:** https://chadoconnor5.github.io/cfb-picks/ (open it on a phone, iPad or computer; nothing to install)

## How the website works

- **Game data** (games, sportsbook lines, opening lines, scores, box scores, weather, team analytics, betting
  history, travel/rest, home field, coaching records, FCS history) is rebuilt by a GitHub Action a few times
  a week and saved in `docs/data/`. It uses [CollegeFootballData](https://collegefootballdata.com) (your key,
  stored as the `CFBD_KEY` repository secret) and [Open-Meteo](https://open-meteo.com) (free, no key).
- **The pick deadline is enforced.** Each week's data keeps updating until Friday 12:00 PM Pacific, then it is
  frozen, so Claude only ever sees what was known by the deadline. A past week built for the first time after
  its deadline is rebuilt "as of" the deadline (stats from earlier games only, the opening line instead of the
  closing line, the forecast issued before the cutoff).
- **Everything you enter** (cards, picks, league entries, Claude's picks, reviews, lessons) is saved to
  `state.json` in the private repo `chadoconnor5/cfb-picks-data` on every connected device (☁ button), so Ron and
  Chad see the same thing everywhere. An unconnected browser keeps its entries locally (Backup / Restore).
- **Claude** runs without an API key: when Chad asks a Claude Code session to "do Claude's picks for week N",
  it reads the card from the data repo, researches every game with web search and saves the picks back
  (procedure in `CLAUDE.md`, tools in `cloud.py`). The site's "Copy for Claude" buttons do the same by hand
  with any Claude chat.

## Each week

1. **Mon/Tue:** open the site and set the season and week (it starts on the current week). On the **Pool** tab,
   paste the pool email and click **Import card**. Check the spreads and totals match the email.
2. Click **Copy whole card for Claude** and then **Copy prompt**. Paste it into a new chat at claude.ai with web
   search on. Copy Claude's reply, paste it into the second box and click **Save Claude's answer**.
3. Make Ron's picks in the pick column (or **Use all Claude picks**), then copy the **Reply text**
   into the email to the pool. Picks lock at the deadline. The Pool tab's picks are Ron O'Connor's league
   entry automatically (the name can be changed under League, then Scoring & highlights).
4. **Sunday:** click **Refresh scores** on the Pool tab. Then **Copy review for Claude** does the weekly
   self-review the same way; its lessons go into the following weeks' prompts.
5. **League tab:** paste each player's picks under **Add picks**. Then use **Weekly sheet** and **Season totals**
   (Download CSV works on both). Enter the league's posted points to let **Scoring & highlights** work out the
   combined-score and 5-point-game rules.

The **❓ Instructions** button (top right of the site) has the same steps.

**Refresh data** reloads the newest data the Action has published. To update the data right now, run the Action
by hand: GitHub repo, then **Actions**, then **Update data**, then **Run workflow**. To rebuild a past week for a
blind trial, type its number (for example `7`) in the **weeks** box.

## Setup (one time)

1. **CFBD key as a secret:** repo **Settings**, then **Secrets and variables**, then **Actions**, then
   **New repository secret**. Use the name `CFBD_KEY` and paste your key as the value.
2. **Turn on the website:** repo **Settings**, then **Pages**. Under "Build and deployment", set Source to
   **Deploy from a branch**, Branch to **main**, folder to **/docs**, and click **Save**.
3. **Shared saving:** create a private repo named `cfb-picks-data` (tick "Add a README file"). Then create a
   fine-grained access key at https://github.com/settings/personal-access-tokens/new: Repository access, then
   Only select repositories, then `cfb-picks-data`; Permissions, then Contents, then Read and write. On the site,
   tap **☁ Connect** and paste it. Then tap ☁ again and **Copy link** to send Ron a link that connects his
   device in one tap (send it privately).
4. **First data build:** **Actions**, then **Update data**, then **Run workflow** (the first run takes about
   10 minutes because it downloads four past seasons of history; later runs take a minute or two).

The schedule (in `.github/workflows/update-data.yml`) runs Monday to Thursday late morning, Friday at about 10 AM
Pacific (the last update before the deadline), and Saturday night and Sunday morning for scores. A run uses
about 15 CFBD calls (the free tier allows 1,000 a month); `docs/data/index.json` keeps a 30-day count.

## Files

- `docs/`: the website (`index.html`, `app.js` UI, `store.js` in-browser storage and endpoints, `logic.js` card
  parsing, grading, league scoring, review stats and prompts) and `docs/data/` (generated).
- `build_site.py`: builds `docs/data/` (run by the Action; `python build_site.py --help`).
- Python modules (`analytics.py`, `conditions.py`, `history.py`, `situational.py`, `coaches.py`, `fcs.py`,
  `weather.py`, `predict.py`, ...): the analysis that produces each game's context.
- `tests/`: `python -m pytest`. `test_js_parity.py` runs the same inputs through `logic.js` (needs Node) and
  the Python, which must agree. `test_build_site.py` runs the data build against `tests/fake_services.py`.

## Running it on your computer instead (optional)

The original local app still works: Python + Flask in `app.py`, UI in `static/`, SQLite.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then fill in CFBD_API_KEY (ANTHROPIC_API_KEY is optional)
python app.py             # http://127.0.0.1:5050
```

Windows (Command Prompt): `python -m venv .venv`, `.venv\Scripts\activate`, `pip install -r requirements.txt`,
`copy .env.example .env`, `notepad .env`, `python app.py`.

The local app keeps its own database; it does not share data with the website. The rest of this README
describes the local app in detail; most of it (pool cards, the league, the review, the cutoff rules) applies
to the website too.

### Copy-for-Claude mode (no API key)

With `ANTHROPIC_API_KEY` blank, every "ask Claude" button becomes **Copy for Claude**: the app builds the full
prompt, you paste it into claude.ai, and paste the answer back. With a key, predictions are one click.

### Weekly workflow (local app)

1. **Sync slate & lines.** Pulls the week's FBS games and lines from your `LINES_PROVIDER`
   (falls back to the first book CFBD has). Re-sync any time to refresh lines. The opening line
   is shown when it differs. Sync also refreshes:
   - **Team analytics** (at most every 12h): pass rate, EPA per play passing and rushing, success rate,
     explosiveness, pace, defense against the pass and the run, havoc and SP+, each ranked as an FBS percentile.
   - **Kickoff weather** for outdoor games within about 15 days: temperature, wind and gusts, rain and
     snow over the game window. Domes are skipped. Re-sync on game day for the sharpest forecast.
2. **Notes.** Add injuries, weather, or motivation per game. They're saved and sent to Claude.
3. **Get Claude's pick.** If "Search injuries before predicting" is checked (and the stored
   report is over 12h old), Claude first searches for injury and availability news. The prediction
   prompt then includes both teams' analytics, the forecast, the style-vs-weather assessment, the
   injury report and your notes. Claude returns the ATS side, O/U, projected score, confidence (50-95),
   2-3 sentences of reasoning, up to 3 key factors, and a one-line **weather & injuries** impact.
   You can re-run it as lines, weather, injuries or notes change.
4. **Save your pick.** Choose an ATS side, an O/U, or both. The line fields come prefilled with the
   current line; edit them if your book has a different number. Saving:
   - stores the exact line you picked against (later line moves don't affect it)
   - locks Claude's latest prediction on that game (no more re-runs)
   - shows whether you **agree** or **fade** Claude.

   *Copy Claude* / *Fade Claude* fill the form in one click. Picks are closed at kickoff. Before
   kickoff you can delete a pick, which also unlocks Claude.
5. **Pull scores & grade.** Fetches final scores and grades everything (sync grades too):
   - your pick is graded on **your** locked line
   - Claude's pick is graded on **the line Claude saw** when it predicted
   - landing exactly on the number is a push.

### Weekly routine

1. **Monday/Tuesday (card arrives):** on the Slate tab, set the week and click **Sync slate & lines**.
   This refreshes games, lines, weather, analytics, the history warehouse (lines, box scores, polls,
   talent, SP+, coaches) and the FBS team check. Then paste the card on the **Pool** tab.
2. **Before Friday noon:** click **Predict all**. For each game Claude gets:
   - the pool line, with the market line beside it
   - team analytics and weather + style
   - injuries and program intel (staff, hot seat, rumors, NIL)
   - head coach records and staff track records
   - situation (travel, rest, look-ahead), home field and crowd
   - betting history, FCS history for new FBS teams
   - lessons from earlier weeks.

   Make your picks and copy the reply text. Picks lock at the deadline.
3. **After the games:** click **Grade card**, then **Run Claude's review**. The Review tab shows:
   - records, confidence calibration, closing-line value, and Claude vs you
   - each pick's verdict: right read, lucky, unlucky or wrong read, with the cause
   - the lessons, which feed next week's prompts (switch any off).

### League leaderboard (League tab)

1. Import the week's card on the Pool tab. Past weeks work too: their games are fetched automatically.
2. On the League tab, type a player's name, paste their reply (same format as the card, with their
   picks) and, if you have it, the league's posted points for that week. Short names are understood:
   "MISS ST", "TENN", "UNC", "WESTERN MICH", "Bama" and so on (`reference/team_nicknames.json`).
   Anything that can't be read is flagged.
3. After **Grade card** (Pool tab), the week table shows each player's points by category, their
   combined-score guess and how far off it was, Player of the Week, and the total. The season
   leaderboard adds up the weeks. Claude is shown separately ("not in league") and never takes POTW.

**Sheets:** the League tab has a **Weekly sheet** and **Season totals**, each with **Download CSV**
(opens in Excel or Google Sheets):
- **Weekly sheet:** every player's pick on every card item, colored green (right) or red (wrong),
  plus points by category, combined-score guess, POTW and total.
- **Season totals:** rank, each week's score, total, points behind the leader, POTW count and pick %.

Rows can be highlighted by player (Scoring & highlights). Defaults: Patrick, Zach, Kevin and Darren
Kenney in light red, Ron O'Connor in gold. Capitalization and curly apostrophes don't matter.

**Scoring (editable under "Scoring rules"):**

| Item | Points |
|---|---|
| Correct pick against the spread | 3 |
| Correct over/under | 1 |
| Correct question | 1 |
| Mid-major (spread only) | 2 |
| N-Point Game | spread pick worth N (assumed) |
| Player of the Week (high score) | +3 |

The combined score is the sum of final scores of every card game, mid-major excluded.

**Unknown rules:** how the combined-score guess earns points, how the 5-point game applies, and how POTW
ties break. Enter the league's posted weekly points for past weeks and click **Figure out the unknown
rules**. Every combination is tested and ranked by how many posted scores it reproduces exactly:
- combined score: none, closest gets 1/2/3/5, exact only, within 5/10, or tiered by rank
- N-Point Game: spread pick worth N, both picks worth N, or 3 + N
- POTW ties: all tied get the bonus, or broken by the combined-score guess

Click **Use** to apply the winner. Question results that can't be graded automatically (e.g. player
stats) can be entered by hand, so POTW isn't blocked.

### Trial run: replaying a past week blind

If a card's deadline has passed, **Predict all** runs a **strict replay** as of the deadline, and
the row shows a REPLAY badge and a leakage audit. Use it to test the system on a week that's over
(e.g. 2026 week 7 with a Friday-noon cutoff):
- **Analytics, betting history, situation, coach records:** rebuilt from games played before the cutoff.
- **Market line:** the opening line; the closing line is hidden.
- **Weather:** the forecast as issued before the cutoff (Open-Meteo Previous Runs API), never what
  actually happened. If it's unavailable, weather is left out, not filled with actuals.
- **Injuries and program intel:** web searches told the cutoff. Intel items dated after it are dropped
  by the app too. Web search can't be date-filtered perfectly, so check the audit and the dated sources.
- **Home field and weather calibration:** completed seasons only. Lessons and calibration: earlier weeks only.

Your own imported picks stay locked; grade and review the replay like any other week.
`reference/snapshots/2026-week7-pre-cutoff-notes.md` holds the preview research gathered before
any week-7 results were seen, for comparison.

### Pool mode (your weekly card)

1. Sync the week on the Slate tab, then open **Pool** and paste the pool email. Lines look like
   `Ohio State @ Indiana (+3.5) o/u 50.5 *5 Point Game*`, where the number in parentheses is the
   **home** team's spread. It also reads `Questions` (numbered over/under props), `Mid-Major Contest`
   (spread only) and `Combined Score -` (the tiebreaker). Pasting a reply that already has picks
   (`Ohio State, U`, `OVER`, `WESTERN MICH`) imports those picks too.
2. Each card game is matched to the slate. Props are interpreted (team, stat, team vs combined),
   e.g. "Northwestern rushing yards" or "Washington State + Oregon State combined total yards".
   Anything unmatched is flagged.
3. **Predict all** asks Claude about every item **against the pool's line, not the sportsbook's**.
   It uses the full context (analytics, weather, injuries, betting history) plus per-game stat logs
   for props.
4. Pick yourself, or use **Use Claude** per row or for all. The tiebreaker defaults to Claude's
   projected combined score of every card game.
5. **Reply text** is your picks in the email's format, ready to paste.
   - **Entering lines:** no email to paste? Click **Start a blank card**, then add games from the
     week's slate and type the pool's spread (home team's number, + = home underdog), O/U and point
     value, plus any questions. Every line stays editable until the deadline. The current sportsbook
     line is shown next to each pool line (flagged "moved" if it changed since the email), and
     Claude is told about the gap. Changing a line after Claude predicted marks that prediction stale.
   - **Deadline:** defaults to Friday 12:00 PM local time before the week's Saturday games, and you
     can edit it. After it passes, picks, lines and Claude runs are locked. A game that kicks off
     before the deadline (e.g. Thursday) locks at its kickoff.
6. **Grade card** after the games: picks are graded against the pool lines, and props from CFBD box
   scores. Scoring assumption: 1 point per correct pick, and both picks in an N-Point Game are worth N.

### Historical spreads and totals

Every game card has a **Betting history** panel, and every prompt gets the same data. It covers
each team's record against the spread and on totals this season and over the last 5 seasons (home,
away, favorite, underdog, conference, last 10), recent head-to-head meetings against the line, and
per-game rushing yards, total yards and sacks (for and allowed), which props need. It comes from
the backtest warehouse (CFBD consensus closing lines). `python -m backtest.collect` builds past
seasons once, and each sync keeps the current season current. Finished weeks are cached
permanently, so a sync costs only a few API calls.

### How weather, style and injuries combine

`conditions.py` is a transparent rules layer, not a black box:

1. **Severity.** The forecast is graded per factor (none / mild / moderate / severe):
   - wind: 10 / 15 / 20+ mph sustained, or gusts of 20 / 28 / 35+
   - rain: by inches per hour and in total; snow by accumulation
   - temperature: 32°F or below, 20°F or below, 90°F or above.

   These roll up into a 0-6 *passing-hostility* score. Wind is weighted most, then precipitation.
2. **Style exposure.** Each offense gets a score built from:
   - pass-rate percentile, plus extra if it relies on explosive passing
   - an adjustment for whether it can fall back on an efficient run game (or can't)
   - for run-first teams, whether the opponent's run defense is stout or soft.

   When conditions are hostile, each team is labeled **exposed**, **neutral** or **insulated**,
   with the reasons spelled out.
3. **Matchup notes.** One team exposed while the other isn't is flagged as a style edge.
   Significant or extreme conditions flag an under lean, and wind adds kicking and punting caveats.
4. **Claude decides.** All of this goes into the prompt as a heuristic. Claude is told to combine it
   with injuries (for example, a pass-heavy team missing its QB is even more fragile in wind, and a
   run team missing linemen loses its insulation) and to overrule it when it has a reason.

The tables and thresholds are easy to tune in `conditions.py`. Each prediction stores the exact
context it saw (profiles, forecast, assessment, injuries) in `predictions.context`.

### Backtest: learning from past weather games

`backtest/` builds a historical sample and measures what weather actually did, by offensive style:

```bash
python -m backtest.collect --seasons 2016-2025   # CFBD games, box scores, EPA, closing lines + archive weather
python -m backtest.analyze                        # -> backtest/report.md, results.json, calibration.json
python -m backtest.find_stories --top 30          # optional: Claude searches recaps of the most extreme games
```

- **Sample:** every FBS game with an exact kickoff time at an outdoor venue (about 7,000 over 10
  seasons). Weather comes from the Open-Meteo historical archive for the four hours from kickoff,
  graded by the same `conditions.py` rules the app uses on forecasts.
- **Effect on a team:** the game's pass rate and EPA (overall, passing, rushing) minus that team's
  own average in its other games that season, split by style and by weather level.
- **Market check:** points vs the closing consensus total and covers vs the closing spread. Closing
  lines already price forecast weather, so this is where a real edge would show.
- **Rule test:** styles are built only from games before each matchup, so "exposed / insulated",
  the style edge and the under lean are tested without look-ahead.
- **Regressions:** offense EPA change and pass-rate change on wind, rain, snow, cold, and wind × pass-heavy.
- **Stories:** `backtest/stories.json` holds reported weather games with sources. The report lines
  them up with the measured weather and each team's stat changes, which shows whether the archive
  data captures what reporters saw. `find_stories` adds candidates to `stories_found.json` for you to review.
- **Feeding the app:** once `backtest/calibration.json` exists, every game card and prediction prompt
  includes the historical evidence for similar conditions (scoring vs total, under rate, how
  similar-style offenses fared, and the rules' own track record), with sample sizes.
  Claude is told to trust this over the rules of thumb.

Costs: about 40 CFBD calls per season (check your CFBD tier's monthly limit) plus one archive call
per venue per season. Every response is cached in `backtest/cache/`, so re-runs are free.

### More context (each game card: "More context")

- **Situation (`situational.py`):**
  - travel miles, time zones crossed and body-clock kickoff time (e.g. "9:00 AM home time")
  - altitude (and the jump vs home), grass vs turf
  - rest days, coming off a bye, short weeks
  - rivalry (`reference/rivalries.json`), conference game
  - revenge spots, look-ahead and letdown spots, bowl-eligibility stakes.
- **Home field and attendance (`backtest/hfa.py`):**
  - league and team home edges vs closing spreads (team edges shrunk so small samples don't mislead)
  - whether fuller-than-normal crowds cover more
  - how wind, rain, snow, cold and heat change turnout, and each game's expected crowd.
- **Coaches (`coaches.py`):**
  - head coach records straight up and ATS: vs ranked, vs top 10, as ranked, bowls, conference,
    rivalry, favorite or underdog, road, after a bye
  - "results vs roster talent" (SP+ vs what the 247 talent composite predicts)
  - staff: each coordinator and position coach's past units, graded by FBS percentile, plus the
    first-year change when they arrived. Staff careers come from the program-intel search.
- **Program intel (`intel.py`, weekly per team, "Program intel" checkbox):** staff and play-callers,
  hot seat (expert ratings and reporting), coaching rumors and changes, NIL roster-budget estimates
  (schools don't disclose these, so they're ranges) and roster news, all dated and sourced.
- **FCS history (`fcs.py`, NCAA API):** prior FCS seasons for programs new to FBS (2026: North
  Dakota State, Sacramento State): record, scoring, results vs FBS opponents, and optionally FCS
  stat ranks after a one-time `python -m fcs discover 2025`.

### Record tab

- Your ATS and O/U records
- Claude's record on games you locked, and on every game it predicted (its latest pick if you didn't pick)
- Your record when you agreed with Claude vs. when you faded it
- Claude's hit rate by confidence bucket (a check on whether its confidence means anything)
- Full pick history

### Configuration (`.env`)

| Variable | Default | |
|---|---|---|
| `CFBD_API_KEY` | (required) | CollegeFootballData bearer token |
| `ANTHROPIC_API_KEY` | (optional) | Claude API key for one-click predictions; blank = copy-for-Claude mode |
| `PICKS_CLAUDE_MODE` | auto | `manual` or `api` |
| `LINES_PROVIDER` | first available | e.g. `DraftKings`, `ESPN Bet`, `Bovada` |
| `PICKS_CLAUDE_MODEL` | `claude-opus-5-5` | Any Claude model id |
| `PICKS_CLAUDE_EFFORT` | `high` | `low` / `medium` / `high` / `xhigh` / `max`. Lower is cheaper and faster |
| `PORT` | `5050` | |

Prediction requests opt into server-side refusal fallback (`fallbacks: "default"`), so a
rare safety-classifier decline is retried on another model instead of failing.

### Notes

- Spreads are stored from the **home** team's perspective (negative = home favored), matching CFBD.
- Claude's own knowledge stops at its training cutoff. The synced analytics, forecast and injury
  search supply the current-season picture, and your notes override everything.
- Each injury search uses Claude's web search tool (billed per search, up to 8 per game) and
  takes 20-60 seconds. Uncheck "Search injuries before predicting" to skip it.
- Open-Meteo is free for non-commercial use.
- CFBD keeps some endpoints for Patreon supporters. If analytics or venues come back empty, the
  sync status shows a warning and predictions still run without them.
- Tests: `pytest` covers the grading math, CFBD parsing, profile percentiles, weather severity,
  style exposure, the forecast window, database migration, and the full sync → injuries → predict →
  pick → grade flow, with mocks.

### Files

```
app.py        Flask routes, locking rules, grading pass, record stats
cfbd.py       CollegeFootballData client (games, lines, calendar)
analytics.py  Team style profiles + FBS percentiles
weather.py    Venues + Open-Meteo kickoff forecast
conditions.py Weather severity + style exposure rules
injuries.py   Claude web-search injury report
predict.py    Claude prompt + JSON schema
backtest/     collect.py, analyze.py, find_stories.py, stories.json (also the history warehouse)
history.py    ATS / O/U records, head-to-head, per-game stat lines
pool.py       Pool card parser, team matching, prop interpretation, export, grading
pool_api.py   Pool routes
league.py     League scoring, Player of the Week, leaderboard, rule inference
league_api.py League routes (entries, week table, leaderboard, scoring, inference)
teams.py      FBS team list + verification against reference/fbs_<season>.json
situational.py  Travel, rest, schedule spots, rivalry, altitude
coaches.py    Head coach splits, talent over/under, staff unit grading
intel.py      Program intel search (staff, hot seat, rumors, NIL), cutoff-enforced
fcs.py        FCS history via the NCAA API
review.py     Weekly self-review: stats, Claude's verdicts, lessons, calibration
backtest/hfa.py  Home field + attendance analysis
reference/    Verified 2026 FBS list with sources; indoor home-stadium overrides
grading.py    ATS / O/U grading math
db.py         SQLite schema + helpers
static/       index.html, app.js, style.css
tests/        pytest suite
```
