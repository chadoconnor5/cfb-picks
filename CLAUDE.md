# CFB Picks: notes for Claude sessions

The website (docs/, GitHub Pages) is used by Ron O'Connor and Chad. Everything they enter is saved to
`state.json` in the private repo `chadoconnor5/cfb-picks-data`. Game data (`docs/data/`) is rebuilt by the
"Update data" GitHub Action. There is no Anthropic API key: **Claude's picks and weekly reviews are made by a
Claude Code session when Chad asks** ("do Claude's picks for week 8", "run the review for week 8").

## Doing Claude's picks for a card

1. Make sure both repos are available: this one, and `chadoconnor5/cfb-picks-data` (attach it with
   `add_repo` with push access if it isn't in the session, clone it next to this repo), then `git pull` both so
   the card and the game data are current.
2. See what's waiting: `python cloud.py --state ../cfb-picks-data/state.json --status`
   (card keys look like `2026|regular|8`).
3. Print the card prompt (the same text the site's "Copy whole card for Claude" gives):
   `python cloud.py --state ../cfb-picks-data/state.json --prompt "2026|regular|8" > /tmp/card.md` and read all of it.
4. Do what the prompt says. Research every item with web search (injuries and availability, coaching news,
   QB changes, weather updates, anything the data can't know). If the prompt says BLIND REPLAY, use only
   sources published before the cutoff and do not look up or use the games' results. Then pick each item
   against the pool line shown, following the "How to pick" rules (confidence 50-95, most 52-65; projected
   score consistent with the picks).
5. Write the answer as the JSON array the prompt asks for (one object per item, keyed by `item` number) to
   `/tmp/answers.json`, then validate it:
   `python cloud.py --state ../cfb-picks-data/state.json --answers /tmp/answers.json --card "2026|regular|8" --patch /tmp/patch.json`
   Fix and rerun if it reports any item errors.
6. Save it (merges only Claude's answers into the latest copy, so edits made on the site are kept):
   ```
   git -C ../cfb-picks-data pull
   python cloud.py --state ../cfb-picks-data/state.json --apply /tmp/patch.json
   git -C ../cfb-picks-data add state.json && git -C ../cfb-picks-data commit -m "Claude's picks: week 8" && git -C ../cfb-picks-data push
   ```
   The picks show up on the site for everyone (Refresh, or reopen the page).
7. Tell Chad the picks in a short table (game, pick, O/U, confidence, one-line reason).

## Weekly review (after the games)

`--review-prompt "<card>"` prints the review prompt (needs graded Claude picks). Answer with the JSON object it
asks for in `/tmp/review.json`, then `--review-answer /tmp/review.json --card "<card>" --patch /tmp/patch.json`
and save it exactly like step 6. Its lessons feed the following weeks' prompts.

## Rules

- Never commit an API key or the site access key. The CFBD key lives only in the `CFBD_KEY` Actions secret.
- `docs/logic.js` must give the same answers as the Python (`tests/test_js_parity.py`); run `python -m pytest`
  after changing either.
- Asset URLs in `docs/index.html` carry `?v=`; bump it when changing docs/*.js or docs/style.css.
