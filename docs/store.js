// The website's "server": the same /api/... endpoints the local Flask app had, answered in the browser.
// Game data comes from data/*.json (rebuilt by GitHub Actions); everything you enter (cards, picks,
// Claude's answers, league entries, reviews) is saved in this browser's localStorage. Use Backup / Restore
// to move it to another device.
(function (root) {
  "use strict";
  const L = root.Logic;
  const KEY = "cfbPicks.v1";
  const DATA = "data/";

  // ---------- saved state ----------

  function blank() {
    return { version: 1, nextId: 1, cards: {}, entries: {}, scoring: null, highlights: null, reviews: {}, lessons: [],
             slate: { notes: {}, predictions: {}, picks: {} }, savedAt: null };
  }

  let S = load();

  function load() {
    try {
      const raw = localStorage.getItem(KEY);
      if (raw) return Object.assign(blank(), JSON.parse(raw));
    } catch (e) { /* fall through */ }
    return blank();
  }

  function save() {
    S.savedAt = new Date().toISOString();
    try { localStorage.setItem(KEY, JSON.stringify(S)); }
    catch (e) { throw new Error("Couldn't save in this browser (storage full or blocked). Download a backup now."); }
  }

  const newId = () => S.nextId++;
  const cardKey = (season, week, type) => `${season}|${type || "regular"}|${week}`;
  const nowIso = () => new Date().toISOString();
  const minuteIso = () => new Date().toISOString().slice(0, 16) + "+00:00";

  // ---------- game data ----------

  const cache = { index: null, weeks: {}, prompts: null, refs: {} };

  async function getJson(path, bust) {
    const res = await fetch(DATA + path + (bust ? `?v=${encodeURIComponent(bust)}` : ""), { cache: bust ? "default" : "no-store" });
    if (!res.ok) return null;
    return res.json();
  }

  async function index(force) {
    if (!cache.index || force) cache.index = (await getJson("index.json")) || { weeks: [], current: null, season: null };
    return cache.index;
  }

  async function weekData(season, week, type, force) {
    const k = cardKey(season, week, type);
    if (cache.weeks[k] && !force) return cache.weeks[k];
    const idx = await index(force);
    const entry = (idx.weeks || []).find((w) => w.season === season && w.week === week && w.season_type === (type || "regular"));
    if (!entry) return (cache.weeks[k] = null);
    const d = await getJson(entry.file, entry.generated_at || idx.generated_at);
    if (d) {
      d.byId = {};
      for (const g of d.games) d.byId[g.id] = g;
    }
    return (cache.weeks[k] = d);
  }

  async function prompts() {
    if (!cache.prompts) cache.prompts = await getJson("prompts.json", (await index()).generated_at);
    if (!cache.prompts) throw new Error("The site's data hasn't been built yet (no data/prompts.json). Run the “Update data” action on GitHub.");
    return cache.prompts;
  }

  async function reference(season) {
    if (!(season in cache.refs)) cache.refs[season] = (await getJson(`reference-${season}.json`, (await index()).generated_at)) || { fbs: {}, nicknames: {} };
    return cache.refs[season];
  }

  async function needWeek(season, week, type) {
    const d = await weekData(season, week, type);
    if (!d) throw new Error(`No game data for ${season} week ${week} yet. The data updates automatically a few times a week; ` +
                            "to update now, run the “Update data” action on GitHub (see README).");
    return d;
  }

  // ---------- helpers ----------

  class ApiError extends Error {}
  const err = (m) => { throw new ApiError(m); };

  const parseIso = (s) => new Date(s);
  const kickedOff = (g) => (g.start_date ? Boolean(g.completed) || Date.now() >= parseIso(g.start_date).getTime() : Boolean(g.completed));
  const pastDeadline = (card) => Boolean(card.deadline) && Date.now() >= parseIso(card.deadline).getTime();
  const lockedItem = (card, g) => pastDeadline(card) || (g ? kickedOff(g) : false);

  function numField(body, key, required) {
    const v = body[key];
    if (v === null || v === undefined || v === "") {
      if (required) err(`${key} is required`);
      return null;
    }
    const n = Number(v);
    if (!Number.isFinite(n)) err(`${key} must be a number`);
    return n;
  }

  function cardById(id) {
    const c = Object.values(S.cards).find((c) => c.id === Number(id));
    if (!c) err("Card not found");
    return c;
  }

  function itemById(id) {
    for (const c of Object.values(S.cards)) {
      const it = c.items.find((i) => i.id === Number(id));
      if (it) return [it, c];
    }
    err("Item not found");
  }

  const sortedItems = (card) => [...card.items].sort((a, b) => a.idx - b.idx);
  const weekKey = (type, week) => [type === "regular" ? 0 : 1, week];
  const keyLess = (a, b) => a[0] < b[0] || (a[0] === b[0] && a[1] < b[1]);

  function poolGame(game, d) {
    const g = { ...game };
    g.spread = d.flipped ? -d.home_spread : d.home_spread;
    g.total = d.total === undefined ? null : d.total;
    g.spread_open = g.total_open = null;
    return g;
  }

  function scoring() { return L.merged(S.scoring); }

  // ---------- pool card payload (results computed from the latest data) ----------

  async function cardPayload(card) {
    const wd = await weekData(card.season, card.week, card.season_type);
    const games = wd ? wd.byId : {};
    const out = [];
    let projected = 0;
    for (const raw of sortedItems(card)) {
      const it = JSON.parse(JSON.stringify(raw));
      const d = it.data;
      const g = games[d.game_id];
      it.game = g ? Object.fromEntries(["id", "start_date", "completed", "home_points", "away_points", "home_team", "away_team",
                                         "spread", "total"].map((k) => [k, g[k] === undefined ? null : g[k]])) : null;
      it.kicked_off = g ? kickedOff(g) : false;
      it.locked = lockedItem(card, g);
      const c = it.claude || null;
      it.claude = c;
      if (g && it.kind !== "prop") {
        it.market = { home_spread: g.spread == null ? null : (d.flipped ? -g.spread : g.spread), total: g.total };
      }
      if (c) {
        it.claude_stale = it.kind === "prop" ? (c.pool_line != null && c.pool_line !== d.line)
          : (c.pool_spread !== d.home_spread || (c.pool_total == null ? null : c.pool_total) !== (d.total == null ? null : d.total));
      }
      // results
      it.ats_result = it.ou_result = it.claude_ats_result = it.claude_ou_result = null;
      it.prop_result = it.claude_prop_result = null;
      if (it.kind === "prop") {
        const actual = it.prop_actual_manual != null ? it.prop_actual_manual : L.propActual(d, games);
        it.prop_actual = actual == null ? null : actual;
        if (actual != null) {
          it.prop_result = L.gradeProp(it.my_prop, d.line, actual);
          it.claude_prop_result = L.gradeProp(c && c.pick, d.line, actual);
        }
      } else if (g && g.completed) {
        [it.ats_result, it.ou_result] = L.gradeGameItem({ ...d, my_ats: it.my_ats, my_ou: it.my_ou }, g);
        [it.claude_ats_result, it.claude_ou_result] = L.gradeGameItem({ ...d, my_ats: c && c.ats_pick, my_ou: c && c.ou_pick }, g);
        if (g.box) {
          const hb = g.box.home || {}, ab = g.box.away || {};
          d.actual = d.flipped ? { home: ab, away: hb } : { home: hb, away: ab };
        }
      }
      if (it.kind === "game" && c && c.proj_home != null) projected += c.proj_home + c.proj_away;
      out.push(it);
    }
    const cfg = scoring();
    const mine = {}, theirs = {};
    for (const it of out) {
      mine[String(it.idx)] = it.kind === "prop" ? { pick: it.my_prop } : { ats: it.my_ats, ou: it.my_ou };
      if (it.claude) theirs[String(it.idx)] = it.kind === "prop" ? { pick: it.claude.pick } : { ats: it.claude.ats_pick, ou: it.claude.ou_pick };
    }
    const my = L.scoreEntry(out, mine, cfg), cl = L.scoreEntry(out, theirs, cfg);
    const picks = {};
    for (const it of out) if (it.kind !== "prop") picks[it.idx] = { ats: it.my_ats, ou: it.my_ou };
    const cardGames = out.filter((it) => it.kind !== "prop").map((it) => ({ ...it.data, idx: it.idx }));
    const props = out.filter((it) => it.kind === "prop").map((it) => ({ ...it.data, pick: it.my_prop }));
    const proj = projected ? L.pyRound(projected) : null;
    const tiebreak = card.tiebreaker != null ? card.tiebreaker : proj;
    return {
      card: { id: card.id, season: card.season, week: card.week, season_type: card.season_type, has_tiebreaker: card.has_tiebreaker ? 1 : 0,
              tiebreaker: card.tiebreaker, deadline: card.deadline, locked: pastDeadline(card) },
      items: out,
      projected_combined: proj,
      score: { points: my.base, graded_possible: my.breakdown.graded, correct: my.breakdown.correct, claude_points: cl.base },
      export: L.exportText(cardGames, picks, props, card.has_tiebreaker ? tiebreak : null),
      tiebreaker_source: card.tiebreaker != null ? "mine" : (projected ? "claude" : null),
      scoring_note: `League scoring: spread ${cfg.ats}, O/U ${cfg.ou}, question ${cfg.prop}, mid-major ${cfg.mid_major}, ` +
        "N-Point Game per the League tab. Combined-score and POTW points show on the League tab.",
      data_note: wd ? null : "No game data for this week yet.",
      owner: owner(),
    };
  }

  function seasonCards(season) {
    return Object.values(S.cards).filter((c) => c.season === season)
      .sort((a, b) => (keyLess(weekKey(a.season_type, a.week), weekKey(b.season_type, b.week)) ? -1 : 1));
  }

  async function seasonRows(season, before) {
    const rows = [];
    for (const c of seasonCards(season)) {
      if (before && !keyLess(weekKey(c.season_type, c.week), before)) continue;
      rows.push(...L.weekItems((await cardPayload(c)).items).map((r) => ({ ...r, week: c.week })));
    }
    return rows;
  }

  async function lessonsFor(season, week, type) {
    const key = weekKey(type || "regular", week);
    const out = S.lessons.filter((l) => l.season === season && l.active && keyLess(weekKey(l.season_type, l.week), key))
      .sort((a, b) => (b.week - a.week) || (b.id - a.id)).slice(0, 10)
      .map((l) => `[week ${l.week}, ${l.applies_to}] ${l.text}`);
    return out.concat(L.calibrationLines(L.stats(await seasonRows(season, key))));
  }

  // ---------- pool routes ----------

  async function importCard(b) {
    const season = Number(b.season), week = Number(b.week), type = b.season_type || "regular";
    if (!season || !week) err("season and week are required");
    const text = (b.text || "").trim();
    if (!text) err("Paste the pool card text.");
    const parsed = L.parseCard(text);
    if (!parsed.games.length) err("No games found. Lines should look like: Ohio State @ Indiana (+3.5) o/u 50.5");
    const wd = await needWeek(season, week, type);
    const ref = (await reference(season)).fbs;
    const wg = wd.games.map((g) => ({ id: g.id, home_team: g.home_team, away_team: g.away_team }));
    L.matchGames(parsed.games, wg, ref);
    const props = parsed.props.map((p) => L.interpretProp(p, wg, ref));
    const key = cardKey(season, week, type);
    const old = S.cards[key];
    if (old && pastDeadline(old)) err("This week's picks are already locked (deadline passed).");
    const card = { id: old ? old.id : newId(), season, week, season_type: type, raw: text, has_tiebreaker: parsed.has_tiebreaker,
                   tiebreaker: parsed.tiebreaker, deadline: (old && old.deadline) || wd.deadline, items: [] };
    let idx = 0;
    const at = minuteIso();
    for (const g of parsed.games) {
      g.line_entered_at = at;
      const keepClaude = old && old.items.find((o) => o.kind !== "prop" && o.data.game_id === g.game_id && g.game_id != null);
      card.items.push({ id: newId(), idx: idx++, kind: g.kind, data: g, my_ats: g.my_ats || null, my_ou: g.my_ou || null,
                        claude: keepClaude ? keepClaude.claude : null });
    }
    for (const p of props) card.items.push({ id: newId(), idx: idx++, kind: "prop", data: p, my_prop: p.my_pick || null, claude: null });
    S.cards[key] = card;
    save();
    const unmatched = parsed.games.filter((g) => !g.game_id).map((g) => `${g.away} @ ${g.home}`)
      .concat(props.filter((p) => !p.team || !p.stat).map((p) => `Question ${p.n}`));
    return { ok: true, card_id: card.id, games: parsed.games.length, props: props.length, unmatched };
  }

  async function newCard(b) {
    const season = Number(b.season), week = Number(b.week), type = b.season_type || "regular";
    const key = cardKey(season, week, type);
    if (S.cards[key]) err("This week already has a card.");
    const wd = await weekData(season, week, type);
    S.cards[key] = { id: newId(), season, week, season_type: type, raw: "", has_tiebreaker: true, tiebreaker: null,
                     deadline: wd ? wd.deadline : null, items: [] };
    save();
    return { ok: true };
  }

  async function addItem(cardId, b) {
    const card = cardById(cardId);
    if (pastDeadline(card)) err("This week's picks are locked (deadline passed).");
    const kind = b.kind || "game";
    const wd = await needWeek(card.season, card.week, card.season_type);
    const idx = card.items.length ? Math.max(...card.items.map((i) => i.idx)) + 1 : 0;
    const at = minuteIso();
    if (kind === "game" || kind === "mid_major") {
      const g = wd.games.find((x) => x.id === Number(b.game_id));
      if (!g) err("Pick a game from this week's slate.");
      card.items.push({ id: newId(), idx, kind, my_ats: null, my_ou: null, claude: null, data: {
        kind, away: g.away_team, home: g.home_team, game_id: g.id, flipped: false, cfbd_away: g.away_team, cfbd_home: g.home_team,
        home_spread: numField(b, "home_spread", true), total: kind === "game" ? numField(b, "total") : null,
        weight: parseInt(b.weight, 10) || 1, line_entered_at: at } });
    } else if (kind === "prop") {
      const q = (b.question || "").trim();
      if (!q) err("Type the question.");
      const n = card.items.filter((i) => i.kind === "prop").length + 1;
      let line = numField(b, "line");
      if (line === null) { const lm = L.LINE_RE.exec(q); line = lm ? parseFloat(lm.groups.line) : null; }
      if (line === null) err("Enter the over/under number.");
      const wg = wd.games.map((g) => ({ id: g.id, home_team: g.home_team, away_team: g.away_team }));
      const data = L.interpretProp({ kind: "prop", n, question: q, line, unit: "", my_pick: null }, wg, (await reference(card.season)).fbs);
      data.line_entered_at = at;
      card.items.push({ id: newId(), idx, kind: "prop", data, my_prop: null, claude: null });
    } else err("kind must be game, mid_major or prop");
    save();
    return { ok: true };
  }

  function editLine(itemId, b) {
    const [it, card] = itemById(itemId);
    if (pastDeadline(card)) err("This week's picks are locked (deadline passed).");
    const d = it.data;
    if (it.kind === "prop") d.line = numField(b, "line", true);
    else {
      d.home_spread = numField(b, "home_spread", true);
      if (it.kind === "game") d.total = numField(b, "total");
      if (b.weight !== undefined && b.weight !== null && b.weight !== "") d.weight = Math.max(1, parseInt(b.weight, 10) || 1);
    }
    d.line_entered_at = minuteIso();
    save();
    return { ok: true };
  }

  function deleteItem(itemId) {
    const [it, card] = itemById(itemId);
    if (pastDeadline(card)) err("This week's picks are locked (deadline passed).");
    card.items = card.items.filter((i) => i !== it);
    save();
    return { ok: true };
  }

  async function pickItem(itemId, b) {
    const [it, card] = itemById(itemId);
    const wd = await weekData(card.season, card.week, card.season_type);
    const g = wd && it.data.game_id != null ? wd.byId[it.data.game_id] : null;
    if (lockedItem(card, g)) err("Picks are locked for this game (pool deadline or kickoff passed).");
    if (it.kind === "prop") {
      const v = b.pick === undefined ? null : b.pick;
      if (![null, "over", "under"].includes(v)) err("pick must be over or under");
      it.my_prop = v;
    } else {
      const ats = b.ats === undefined ? null : b.ats, ou = b.ou === undefined ? null : b.ou;
      if (![null, "home", "away"].includes(ats) || ![null, "over", "under"].includes(ou)) err("Invalid pick");
      it.my_ats = ats; it.my_ou = ou;
    }
    save();
    return { ok: true };
  }

  function setDeadline(cardId, b) {
    const card = cardById(cardId);
    let v = b.deadline;
    if (v) {
      const d = new Date(v);
      if (isNaN(d)) err("Invalid deadline");
      v = d.toISOString().slice(0, 16) + "+00:00";
    }
    card.deadline = v || null;
    save();
    return { ok: true };
  }

  function setPropActual(itemId, b) {
    const [it] = itemById(itemId);
    if (it.kind !== "prop") err("Only questions take a manual result.");
    it.prop_actual_manual = numField(b, "value");
    save();
    return { ok: true };
  }

  function setTiebreaker(cardId, b) {
    const card = cardById(cardId);
    if (pastDeadline(card)) err("This week's picks are locked (deadline passed).");
    const v = b.value;
    let n = null;
    if (v !== null && v !== undefined && v !== "") {
      n = Number(v);
      if (!Number.isInteger(n)) err("Tiebreaker must be a whole number");
    }
    card.tiebreaker = n;
    card.has_tiebreaker = true;
    save();
    return { ok: true };
  }

  async function gradeCard(cardId) {
    const card = cardById(cardId);
    await weekData(card.season, card.week, card.season_type, true);
    const p = await cardPayload(card);
    const graded = p.items.filter((it) => it.ats_result || it.ou_result || it.prop_result || it.claude_ats_result ||
                                         it.claude_prop_result || (it.kind === "prop" && it.prop_actual != null)).length;
    return { graded };
  }

  // Items Claude can be asked about: matched to a game, and (before the deadline) not kicked off yet.
  async function eligible(card, only) {
    const wd = await needWeek(card.season, card.week, card.season_type);
    const replay = pastDeadline(card);
    return sortedItems(card).filter((it) => {
      if (only && !only.has(it.id)) return false;
      const g = wd.byId[it.data.game_id];
      return g && (replay || !kickedOff(g));
    });
  }

  const numbering = (card) => new Map(sortedItems(card).map((it, i) => [it.id, i + 1]));

  function marketFor(card, game) {
    const ctx = game.ctx || {};
    if (pastDeadline(card)) {
      const line = ctx.line || { spread: game.spread_open, total: game.total_open, label: "opening line" };
      return { home: game.home_team, spread: line.spread, total: line.total,
               label: ctx.replay ? line.label : "line as of the pool deadline" };
    }
    return { home: game.home_team, spread: game.spread, total: game.total };
  }

  function notesLine(gameId) {
    const n = (S.slate.notes[gameId] || "").trim();
    return `My notes: ${n || "(none)"}`;
  }

  async function cardPrompt(cardId, query) {
    const card = cardById(cardId);
    const only = query.get("items") ? new Set(query.get("items").split(",").map(Number).filter(Boolean)) : null;
    const items = await eligible(card, only);
    if (!items.length) err("Nothing to predict: no items matched to an un-kicked-off game.");
    const wd = await weekData(card.season, card.week, card.season_type);
    const P = await prompts();
    const num = numbering(card);
    const replay = pastDeadline(card);
    const sections = [];
    for (const it of items) {
      const d = it.data, game = wd.byId[d.game_id], ctx = game.ctx || null;
      const market = marketFor(card, game);
      let text;
      if (it.kind === "prop") {
        if (d.line == null) continue;
        const g = { ...game, spread: replay ? market.spread : game.spread, total: replay ? market.total : game.total };
        text = L.propPrompt(d, g, ctx && ctx.prop_stats, ctx && ctx.weather_text, P.stat_labels);
      } else {
        const pg = poolGame(game, d);
        text = d.flipped ? `(Note: the card lists ${d.away} at ${d.home}; below uses the official home team. ` +
          "Name the team you pick, so orientation doesn't matter.)\n" : "";
        text += L.headerLines(pg, market).join("\n") + "\n" +
          (ctx ? ctx.text : "\n(No pre-game data was saved for this game; research it.)") + "\n\n" + notesLine(game.id);
      }
      sections.push({ idx: num.get(it.id), kind: it.kind, teams: [game.home_team, game.away_team], text });
    }
    if (!sections.length) err("Nothing to predict (questions need an over/under number).");
    const title = `${card.season} week ${card.week} pick'em card` + (replay ? " (BLIND REPLAY)" : "");
    const asOf = replay ? card.deadline : null;
    return { prompt: L.cardPrompt(sections, { asOf, replay, title, lessons: await lessonsFor(card.season, card.week, card.season_type), prompts: P }),
             items: sections.map((s) => s.idx), replay, as_of: asOf || nowIso() };
  }

  function gameResult(d, row) {
    const flip = d.flipped;
    let side = row.ats_pick;
    if (flip) side = side === "home" ? "away" : "home";
    return { ats_pick: side, ou_pick: d.total != null ? row.ou_pick : null,
             proj_home: flip ? row.proj_away : row.proj_home, proj_away: flip ? row.proj_home : row.proj_away,
             confidence: row.confidence, reasoning: row.reasoning, key_factors: row.key_factors,
             conditions_impact: row.conditions_impact, model: row.model, pool_spread: d.home_spread, pool_total: d.total == null ? null : d.total };
  }

  async function cardAnswer(cardId, b) {
    const card = cardById(cardId);
    let answers;
    try { answers = L.answersByItem(L.extractJson(b.answer)); } catch (e) { err(e.message); }
    const ids = new Set((await eligible(card, null)).map((it) => it.id));
    const num = numbering(card);
    const byNum = new Map([...num.entries()].map(([id, n]) => [n, id]));
    if (answers.size === 1 && answers.has(null) && b.item_id) answers = new Map([[num.get(Number(b.item_id)), answers.get(null)]]);
    const wd = await weekData(card.season, card.week, card.season_type);
    const replay = pastDeadline(card);
    const saved = [], errors = [];
    const order = [...answers.keys()].sort((x, y) => (x === null) - (y === null) || (x || 0) - (y || 0));
    for (const n of order) {
      const obj = answers.get(n);
      const id = byNum.get(n);
      if (id === undefined) { errors.push(`Item ${n}: no such item on the card.`); continue; }
      if (!ids.has(id)) { errors.push(`Item ${n}: locked (kicked off) or not matched to a game; skipped.`); continue; }
      const it = card.items.find((i) => i.id === id);
      const d = it.data, game = wd.byId[d.game_id];
      try {
        let res;
        if (it.kind === "prop") { res = L.propFromData(obj, "claude (chat)"); res.pool_line = d.line; }
        else res = gameResult(d, L.rowFromData(poolGame(game, d), obj, "claude (chat)"));
        const ctx = game.ctx || {};
        res.replay = replay;
        res.as_of = replay ? card.deadline : minuteIso();
        res.audit = replay ? [...(ctx.audit || []), "Answer pasted from a Claude chat; the prompt was built with this cutoff."] : [];
        res.predicted_at = minuteIso();
        it.claude = res;
        saved.push(n);
      } catch (e) { errors.push(`Item ${n}: ${e.message}`); }
    }
    if (!saved.length && errors.length) err("Nothing saved. " + errors.join(" "));
    save();
    return { saved, errors };
  }

  async function reviewInputs(cardId) {
    const card = cardById(cardId);
    const rows = L.weekItems((await cardPayload(card)).items);
    if (!rows.some((r) => ["W", "L", "P"].includes(r.result))) err("Grade the card first (no graded Claude picks yet).");
    return { card, rows, weekStats: L.stats(rows), prior: await lessonsFor(card.season, card.week, card.season_type) };
  }

  async function reviewPrompt(cardId) {
    const { card, rows, weekStats, prior } = await reviewInputs(cardId);
    return { prompt: L.reviewPrompt(rows, weekStats, card.season, card.week, prior, await prompts()) };
  }

  async function reviewAnswer(cardId, b) {
    const { card, weekStats } = await reviewInputs(cardId);
    let out;
    try { out = L.finalizeReview(L.extractJson(b.answer)); } catch (e) { err(e.message); }
    const key = cardKey(card.season, card.week, card.season_type);
    const data = { stats: weekStats, ...out, created_at: nowIso() };
    S.reviews[key] = data;
    S.lessons = S.lessons.filter((l) => l.card_key !== key);
    for (const l of out.lessons) {
      S.lessons.push({ id: newId(), card_key: key, season: card.season, week: card.week, season_type: card.season_type,
                       text: l.text, applies_to: l.applies_to, evidence: l.evidence, active: 1, created_at: nowIso() });
    }
    save();
    return data;
  }

  async function seasonReview(season) {
    const weeks = [];
    for (const c of seasonCards(season)) {
      const rows = L.weekItems((await cardPayload(c)).items);
      weeks.push({ card_id: c.id, week: c.week, season_type: c.season_type, stats: L.stats(rows),
                   review: S.reviews[cardKey(c.season, c.week, c.season_type)] || null });
    }
    const seasonStats = L.stats(await seasonRows(season));
    const lessons = S.lessons.filter((l) => l.season === season).sort((a, b) => (b.week - a.week) || (a.id - b.id));
    return { season, season_stats: seasonStats, weeks, lessons, calibration_lines: L.calibrationLines(seasonStats) };
  }

  async function poolRecord(season) {
    const weeks = [];
    const totals = {};
    for (const k of ["ats", "ou", "prop", "claude_ats", "claude_ou", "claude_prop"]) totals[k] = { W: 0, L: 0, P: 0 };
    for (const c of seasonCards(season)) {
      const p = await cardPayload(c);
      weeks.push({ week: c.week, season_type: c.season_type, ...p.score });
      for (const it of p.items) {
        for (const [k, col] of [["ats", "ats_result"], ["ou", "ou_result"], ["prop", "prop_result"], ["claude_ats", "claude_ats_result"],
                                ["claude_ou", "claude_ou_result"], ["claude_prop", "claude_prop_result"]]) {
          if (it[col] in totals[k]) totals[k][it[col]] += 1;
        }
      }
    }
    return { season, weeks, totals };
  }

  // ---------- league routes ----------

  // The Pool tab's picks are the owner's (Ron O'Connor by default): his league entry comes from them, so he
  // never has to be pasted in. A pasted entry under the same name replaces it.
  const owner = () => S.owner || "Ron O'Connor";

  function ownerEntry(card, items) {
    const picks = {};
    let any = false, proj = 0;
    for (const it of items) {
      if (it.kind === "prop") { picks[String(it.idx)] = { pick: it.my_prop || null }; any = any || Boolean(it.my_prop); }
      else { picks[String(it.idx)] = { ats: it.my_ats || null, ou: it.my_ou || null }; any = any || Boolean(it.my_ats || it.my_ou); }
      if (it.kind === "game" && it.claude && it.claude.proj_home != null) proj += it.claude.proj_home + it.claude.proj_away;
    }
    if (!any) return null;
    const tb = card.tiebreaker != null ? card.tiebreaker : (card.has_tiebreaker && proj ? L.pyRound(proj) : null);
    return { id: null, player: owner(), picks, tiebreaker: tb, reported_points: card.owner_reported_points ?? null,
             warnings: [], from_pool: true };
  }

  function allEntries(card, items) {
    const pasted = entriesFor(card);
    const mine = ownerEntry(card, items);
    if (mine && !pasted.some((e) => L.normName(e.player) === L.normName(mine.player))) pasted.push(mine);
    return pasted.sort((a, b) => (a.player < b.player ? -1 : a.player > b.player ? 1 : 0));
  }

  function entriesFor(card) {
    const m = S.entries[cardKey(card.season, card.week, card.season_type)] || {};
    return Object.entries(m).map(([player, e]) => ({ id: e.id, player, picks: e.picks, tiebreaker: e.tiebreaker,
                                                     reported_points: e.reported_points, warnings: e.warnings || [] }))
      .sort((a, b) => (a.player < b.player ? -1 : a.player > b.player ? 1 : 0));
  }

  function findCard(q) {
    const season = Number(q.get ? q.get("season") : q.season), week = Number(q.get ? q.get("week") : q.week);
    if (!season || !week) err("season and week are required");
    const type = (q.get ? q.get("season_type") : q.season_type) || "regular";
    const card = S.cards[cardKey(season, week, type)];
    if (!card) err("No card for this week yet. Import the week's card on the Pool tab first.");
    return card;
  }

  function claudeEntry(items) {
    const picks = {};
    let proj = 0;
    for (const it of items) {
      const c = it.claude;
      if (!c) continue;
      if (it.kind === "prop") picks[String(it.idx)] = { pick: c.pick };
      else {
        picks[String(it.idx)] = { ats: c.ats_pick, ou: c.ou_pick };
        if (it.kind === "game" && c.proj_home != null) proj += c.proj_home + c.proj_away;
      }
    }
    return Object.keys(picks).length ? { player: "Claude (not in league)", picks, tiebreaker: proj ? L.pyRound(proj) : null } : null;
  }

  async function weekScored(card, cfg) {
    const items = (await cardPayload(card)).items;
    const entries = allEntries(card, items);
    const scored = L.scoreWeek(items, entries, cfg);
    const ce = claudeEntry(items);
    if (ce) {   // scored on its own so it never takes Player of the Week from a real member
      const alone = L.scoreWeek(items, [ce], { ...cfg, potw_bonus: 0 }).rows[0];
      const guesses = {};
      for (const r of scored.rows) guesses[r.player] = r.tiebreaker;
      guesses[ce.player] = ce.tiebreaker;
      alone.tiebreak_points = L.tiebreakPoints(cfg.tiebreak, guesses, scored.actual_combined)[ce.player] || 0;
      alone.potw = false; alone.potw_points = 0;
      alone.total = alone.base + alone.tiebreak_points;
      scored.claude = alone;
    }
    return { items, entries, scored };
  }

  async function addEntry(b) {
    const name = (b.name || "").trim(), text = (b.text || "").trim();
    if (!name || !text) err("Enter the player's name and paste their picks.");
    const card = findCard(b);
    const items = (await cardPayload(card)).items;
    const ref = await reference(card.season);
    const parsed = L.parseEntry(text, items, ref.fbs, ref.nicknames);
    if (!Object.keys(parsed.picks).length) err("Couldn't find any picks in that text.");
    const key = cardKey(card.season, card.week, card.season_type);
    S.entries[key] = S.entries[key] || {};
    const existing = Object.entries(S.entries[key]).find(([p]) => p === name);
    const rp = b.reported_points;
    S.entries[key][name] = { id: existing ? existing[1].id : newId(), raw: text, picks: parsed.picks, tiebreaker: parsed.tiebreaker,
                             reported_points: rp !== undefined && rp !== null && rp !== "" ? Number(rp) : (existing ? existing[1].reported_points : null),
                             warnings: parsed.warnings };
    save();
    return { ok: true, player: name, picks: Object.keys(parsed.picks).length, tiebreaker: parsed.tiebreaker, warnings: parsed.warnings };
  }

  function entryById(id) {
    for (const m of Object.values(S.entries)) for (const [p, e] of Object.entries(m)) if (e.id === Number(id)) return [m, p, e];
    err("Entry not found");
  }

  function editEntry(id, b) {
    const [, , e] = entryById(id);
    if ("reported_points" in b) e.reported_points = b.reported_points !== null && b.reported_points !== "" ? Number(b.reported_points) : null;
    if ("tiebreaker" in b) e.tiebreaker = b.tiebreaker !== null && b.tiebreaker !== "" ? parseInt(b.tiebreaker, 10) : null;
    save();
    return { ok: true };
  }

  function deleteEntry(id) {
    const [m, p] = entryById(id);
    delete m[p];
    save();
    return { ok: true };
  }

  async function weekView(q) {
    const card = findCard(q);
    const cfg = scoring();
    const { items, entries, scored } = await weekScored(card, cfg);
    const by = {};
    for (const e of entries) by[e.player] = e;
    for (const r of scored.rows) {
      const e = by[r.player] || {};
      r.entry_id = e.id; r.reported_points = e.reported_points === undefined ? null : e.reported_points; r.warnings = e.warnings || [];
      r.from_pool = Boolean(e.from_pool);
    }
    const labels = items.map((it) => ({ idx: it.idx, kind: it.kind, label: it.kind === "prop" ? `Q${it.data.n}` : `${it.data.away} @ ${it.data.home}`,
                                        home: it.data.home || null, away: it.data.away || null, weight: it.data.weight || 1 }));
    return { card_id: card.id, week: card.week, items: labels, scoring: cfg, owner: owner(), ...scored };
  }

  async function seasonWeeks(season, cfg) {
    const weeks = [];
    let claudeTotal = 0;
    for (const card of seasonCards(season)) {
      const { entries, scored } = await weekScored(card, cfg);
      if (entries.length) weeks.push({ week: card.week, season_type: card.season_type, complete: scored.complete, rows: scored.rows,
                                       actual_combined: scored.actual_combined, potw: scored.rows.filter((r) => r.potw).map((r) => r.player) });
      if (scored.claude) claudeTotal += scored.claude.total;
    }
    return { weeks, claudeTotal };
  }

  async function leaderboardView(season) {
    const cfg = scoring();
    const { weeks, claudeTotal } = await seasonWeeks(season, cfg);
    return { season, scoring: cfg, standings: L.leaderboard(weeks),
             weeks: weeks.map((w) => ({ week: w.week, season_type: w.season_type, complete: w.complete, actual_combined: w.actual_combined, potw: w.potw })),
             claude_total: claudeTotal };
  }

  async function inferView(season) {
    const cfg = scoring();
    const weeks = [];
    for (const card of seasonCards(season)) {
      const items = (await cardPayload(card)).items;
      weeks.push({ week: card.week, items, entries: allEntries(card, items) });
    }
    return L.inferRules(weeks, cfg);
  }

  const highlights = () => (S.highlights ? S.highlights : { ...L.DEFAULT_HIGHLIGHTS });

  async function weekSheetView(q) {
    const card = findCard(q);
    const cfg = scoring();
    const { items, entries, scored } = await weekScored(card, cfg);
    const sheet = L.weekSheet(items, scored, entries, highlights());
    if (q.get("format") === "csv") {
      const header = ["Rank", "Player"].concat(sheet.columns.map((c) => `${c.label} (${c.line})` + (c.weight > 1 ? ` [${c.weight} pts]` : "")),
        ["Spread pts", "O/U pts", "Bonus game pts", "Question pts", "Mid-major pts", "Combined guess", "Off by", "Combined pts", "POTW", "Total"]);
      const rows = sheet.rows.map((r, i) => {
        const b = r.breakdown;
        const cells = r.cells.map((c) => (c.label ? `${c.label} (${[c.ats_result, c.ou_result].filter(Boolean).join("/") || "-"})` : ""));
        return [i + 1, r.player, ...cells, b.ats, b.ou, b.bonus_game, b.prop, b.mid_major, r.tiebreaker, r.distance, r.tiebreak_points,
                r.potw ? "YES" : "", r.total];
      });
      return { csv: L.toCsv(header, rows), filename: `week${card.week}_${card.season}.csv` };
    }
    return { week: card.week, season: card.season, actual_combined: scored.actual_combined, complete: scored.complete, ...sheet };
  }

  async function seasonSheetView(q) {
    const season = Number(q.get("season"));
    const cfg = scoring();
    const { weeks } = await seasonWeeks(season, cfg);
    const sheet = L.seasonSheet(L.leaderboard(weeks), weeks, highlights());
    if (q.get("format") === "csv") {
      const header = ["Rank", "Player"].concat(sheet.weeks.map((w) => `Week ${w}`), ["Total", "Behind", "POTW", "Pick %"]);
      const rows = sheet.rows.map((r) => [r.rank, r.player, ...r.weeks_cells.map((v) => (v == null ? "" : v)), r.total, r.behind, r.potw, r.pick_pct]);
      return { csv: L.toCsv(header, rows), filename: `season_${season}.csv` };
    }
    return { season, ...sheet };
  }

  // ---------- slate routes ----------

  function predictionResult(p, g) {
    if (!g || !g.completed || g.home_points == null) return [null, null];
    return [L.gradeAts(p.ats_pick, p.spread, g.home_points, g.away_points), L.gradeOu(p.ou_pick, p.total, g.home_points, g.away_points)];
  }

  async function listGames(q) {
    const season = Number(q.get("season")), week = Number(q.get("week")), type = q.get("season_type") || "regular";
    const wd = await weekData(season, week, type);
    if (!wd) return [];
    return wd.games.map((g) => {
      const preds = S.slate.predictions[g.id] || [];
      const pick = S.slate.picks[g.id] || null;
      let pred = pick && pick.prediction_id ? preds.find((p) => p.id === pick.prediction_id) : preds[preds.length - 1];
      if (pred) {
        pred = { ...pred, locked: pick && pick.prediction_id === pred.id ? 1 : 0 };
        [pred.ats_result, pred.ou_result] = predictionResult(pred, g);
      }
      let pk = null;
      if (pick) {
        pk = { ...pick };
        [pk.ats_result, pk.ou_result] = predictionResult(pick, g);
      }
      const s = g.summary || {};
      return { ...g, ctx: undefined, summary: undefined, kicked_off: kickedOff(g), pick: pk, prediction: pred || null,
               notes: S.slate.notes[g.id] || "", assessment: s.assessment || null, injuries: null,
               profiles: s.profiles || { home: null, away: null }, history: s.history || null,
               has_context: Boolean(g.ctx), context_frozen: Boolean(g.ctx && g.ctx.frozen) };
    });
  }

  async function gameById(id) {
    const idx = await index();
    for (const w of idx.weeks || []) {
      const d = await weekData(w.season, w.week, w.season_type);
      if (d && d.byId[id]) return d.byId[id];
    }
    err("Game not found");
  }

  async function gameContext(id) {
    const g = await gameById(Number(id));
    const c = g.ctx;
    return c ? { text: c.text, audit: c.audit || [], as_of: c.as_of, replay: c.replay, frozen: c.frozen }
      : { text: null, audit: [], as_of: null, replay: false, frozen: false };
  }

  async function gamePrompt(id, b) {
    const g = await gameById(Number(id));
    if (S.slate.picks[g.id]) err("Your pick is saved, so Claude's pick is locked.");
    if (kickedOff(g)) err("This game has already kicked off.");
    if (b.notes !== undefined) { S.slate.notes[g.id] = b.notes; save(); }
    if (g.spread == null && g.total == null) err("No line for this game yet.");
    const text = L.headerLines(g, null).join("\n") + "\n" + (g.ctx ? g.ctx.text : "\n(No pre-game data was saved for this game; research it.)") +
      "\n\n" + notesLine(g.id);
    const P = await prompts();
    return { prompt: L.cardPrompt([{ idx: 1, kind: "game", teams: [g.home_team, g.away_team], text }],
                                  { asOf: null, replay: false, title: `${g.away_team} at ${g.home_team}`,
                                    lessons: await lessonsFor(g.season, g.week, g.season_type), prompts: P }) };
  }

  async function gameAnswer(id, b) {
    const g = await gameById(Number(id));
    if (S.slate.picks[g.id]) err("Your pick is saved, so Claude's pick is locked.");
    if (kickedOff(g)) err("This game has already kicked off.");
    let row;
    try {
      const answers = L.answersByItem(L.extractJson(b.answer));
      row = L.rowFromData(g, [...answers.values()][0], "claude (chat)");
    } catch (e) { err(e.message); }
    const pred = { id: newId(), game_id: g.id, spread: g.spread, total: g.total, notes: S.slate.notes[g.id] || "", ...row, created_at: nowIso() };
    (S.slate.predictions[g.id] = S.slate.predictions[g.id] || []).push(pred);
    save();
    return pred;
  }

  async function savePick(id, b) {
    const g = await gameById(Number(id));
    if (kickedOff(g)) err("This game has already kicked off.");
    const ats = b.ats_pick || null, ou = b.ou_pick || null;
    if (!ats && !ou) err("Pick a side, a total, or both.");
    if (![null, "home", "away"].includes(ats) || ![null, "over", "under"].includes(ou)) err("Invalid pick");
    const spread = numField(b, "spread"), total = numField(b, "total");
    if (ats && spread === null) err("Enter the spread you're taking.");
    if (ou && total === null) err("Enter the total you're taking.");
    const preds = S.slate.predictions[g.id] || [];
    const last = preds[preds.length - 1];
    S.slate.picks[g.id] = { game_id: g.id, prediction_id: last ? last.id : null, ats_pick: ats, ou_pick: ou, spread, total, created_at: nowIso() };
    save();
    return { ok: true, locked_prediction_id: last ? last.id : null };
  }

  async function deletePick(id) {
    const g = await gameById(Number(id));
    if (kickedOff(g)) err("Picks can't be removed after kickoff.");
    if (!S.slate.picks[g.id]) err("No pick to delete");
    delete S.slate.picks[g.id];
    save();
    return { ok: true };
  }

  function tally(results) {
    const t = { W: 0, L: 0, P: 0 };
    for (const r of results) if (r in t) t[r] += 1;
    const decided = t.W + t.L;
    t.pct = decided ? L.pyRound((100 * t.W) / decided, 1) : null;
    return t;
  }

  async function record(season) {
    const idx = await index();
    const games = {};
    for (const w of idx.weeks || []) {
      if (season && w.season !== season) continue;
      const d = await weekData(w.season, w.week, w.season_type);
      if (d) for (const g of d.games) games[g.id] = g;
    }
    const picks = [];
    for (const [gid, pk] of Object.entries(S.slate.picks)) {
      const g = games[gid];
      if (!g) continue;
      const pred = pk.prediction_id ? (S.slate.predictions[gid] || []).find((p) => p.id === pk.prediction_id) : null;
      const [ar, or] = predictionResult(pk, g);
      const [car, cor] = pred ? predictionResult(pred, g) : [null, null];
      picks.push({ ...pk, ats_result: ar, ou_result: or, season: g.season, week: g.week, season_type: g.season_type, home_team: g.home_team,
                   away_team: g.away_team, start_date: g.start_date, home_points: g.home_points, away_points: g.away_points, completed: g.completed,
                   c_ats: pred ? pred.ats_pick : null, c_ou: pred ? pred.ou_pick : null, c_spread: pred ? pred.spread : null,
                   c_total: pred ? pred.total : null, c_conf: pred ? pred.confidence : null, c_ats_result: car, c_ou_result: cor });
    }
    picks.sort((a, b) => (b.start_date || "").localeCompare(a.start_date || ""));
    const claude = [];
    for (const [gid, preds] of Object.entries(S.slate.predictions)) {
      const g = games[gid];
      if (!g || !g.completed || !preds.length) continue;
      const pk = S.slate.picks[gid];
      const p = (pk && pk.prediction_id && preds.find((x) => x.id === pk.prediction_id)) || preds[preds.length - 1];
      const [ar, or] = predictionResult(p, g);
      claude.push({ ...p, ats_result: ar, ou_result: or });
    }
    const locked = picks.filter((p) => p.prediction_id);
    const split = (key, ck) => [locked.filter((p) => p[key] && p[key] === p[ck]), locked.filter((p) => p[key] && p[ck] && p[key] !== p[ck])];
    const [atsAgree, atsFade] = split("ats_pick", "c_ats");
    const [ouAgree, ouFade] = split("ou_pick", "c_ou");
    const buckets = [[50, 59], [60, 69], [70, 79], [80, 95]].map(([lo, hi]) => {
      const rows = claude.filter((c) => c.confidence != null && lo <= c.confidence && c.confidence <= hi);
      return { range: `${lo}-${hi}`, ats: tally(rows.map((c) => c.ats_result)), ou: tally(rows.map((c) => c.ou_result)) };
    });
    return {
      me: { ats: tally(picks.map((p) => p.ats_result)), ou: tally(picks.map((p) => p.ou_result)) },
      claude_locked: { ats: tally(locked.map((p) => p.c_ats_result)), ou: tally(locked.map((p) => p.c_ou_result)) },
      claude_all: { ats: tally(claude.map((c) => c.ats_result)), ou: tally(claude.map((c) => c.ou_result)) },
      agree: { ats: tally(atsAgree.map((p) => p.ats_result)), ou: tally(ouAgree.map((p) => p.ou_result)) },
      fade: { ats: tally(atsFade.map((p) => p.ats_result)), ou: tally(ouFade.map((p) => p.ou_result)) },
      confidence: buckets, history: picks,
    };
  }

  async function teamsView(season) {
    const ref = (await reference(season)).fbs || {};
    const notes = ref.program_notes || {};
    const conferences = {};
    let count = 0;
    for (const conf of Object.keys(ref.conferences || {}).sort()) {
      conferences[conf] = [...ref.conferences[conf]].sort().map((t) => ({ school: t, conference: conf, note: notes[t] || null }));
      count += conferences[conf].length;
    }
    return { season, source: "reference", count, conferences, sources: ref.sources || [],
             check: { checked: false, message: count ? `The verified ${season} FBS list (${count} teams).` : `No verified team list for ${season}.` } };
  }

  async function currentWeek() {
    const idx = await index(true);
    const cur = idx.current || null;
    if (cur) return { season: idx.season, week: cur.week, season_type: cur.season_type };
    const last = (idx.weeks || []).slice(-1)[0];
    return last ? { season: last.season, week: last.week, season_type: last.season_type }
      : { season: idx.season, week: null, season_type: "regular", warning: "No game data yet. Run the “Update data” action on GitHub (see README)." };
  }

  async function refresh(q) {
    const idx = await index(true);
    cache.weeks = {};
    cache.prompts = null;
    const season = Number(q.season), week = Number(q.week), type = q.season_type || "regular";
    const wd = season && week ? await weekData(season, week, type) : null;
    const games = wd ? wd.games : [];
    return { games: games.length, with_lines: games.filter((g) => g.spread != null || g.total != null).length,
             forecasts: games.filter((g) => g.weather).length, completed: games.filter((g) => g.completed).length,
             generated_at: idx.generated_at, week_generated_at: wd ? wd.generated_at : null, frozen: wd ? wd.frozen : null };
  }

  // ---------- router ----------

  const routes = [
    ["GET", /^\/api\/config$/, () => ({ claude_mode: "manual" })],
    ["GET", /^\/api\/current-week$/, () => currentWeek()],
    ["GET", /^\/api\/games$/, (m, q) => listGames(q)],
    ["POST", /^\/api\/(sync|grade)$/, (m, q, b) => refresh(b)],
    ["POST", /^\/api\/games\/(\d+)\/notes$/, (m, q, b) => { S.slate.notes[m[1]] = b.notes || ""; save(); return { ok: true }; }],
    ["GET", /^\/api\/games\/(\d+)\/context$/, (m) => gameContext(m[1])],
    ["POST", /^\/api\/games\/(\d+)\/prompt$/, (m, q, b) => gamePrompt(m[1], b)],
    ["POST", /^\/api\/games\/(\d+)\/answer$/, (m, q, b) => gameAnswer(m[1], b)],
    ["POST", /^\/api\/games\/(\d+)\/pick$/, (m, q, b) => savePick(m[1], b)],
    ["DELETE", /^\/api\/games\/(\d+)\/pick$/, (m) => deletePick(m[1])],
    ["GET", /^\/api\/record$/, (m, q) => record(Number(q.get("season")) || null)],
    ["GET", /^\/api\/teams$/, (m, q) => teamsView(Number(q.get("season")))],
    ["POST", /^\/api\/pool\/import$/, (m, q, b) => importCard(b)],
    ["GET", /^\/api\/pool$/, async (m, q) => {
      const card = S.cards[cardKey(Number(q.get("season")), Number(q.get("week")), q.get("season_type") || "regular")];
      return card ? cardPayload(card) : { card: null };
    }],
    ["POST", /^\/api\/pool\/cards$/, (m, q, b) => newCard(b)],
    ["POST", /^\/api\/pool\/cards\/(\d+)\/items$/, (m, q, b) => addItem(m[1], b)],
    ["POST", /^\/api\/pool\/cards\/(\d+)\/deadline$/, (m, q, b) => setDeadline(m[1], b)],
    ["POST", /^\/api\/pool\/cards\/(\d+)\/tiebreaker$/, (m, q, b) => setTiebreaker(m[1], b)],
    ["POST", /^\/api\/pool\/cards\/(\d+)\/grade$/, (m) => gradeCard(m[1])],
    ["GET", /^\/api\/pool\/cards\/(\d+)\/prompt$/, (m, q) => cardPrompt(m[1], q)],
    ["POST", /^\/api\/pool\/cards\/(\d+)\/answer$/, (m, q, b) => cardAnswer(m[1], b)],
    ["GET", /^\/api\/pool\/cards\/(\d+)\/review\/prompt$/, (m) => reviewPrompt(m[1])],
    ["POST", /^\/api\/pool\/cards\/(\d+)\/review\/answer$/, (m, q, b) => reviewAnswer(m[1], b)],
    ["POST", /^\/api\/pool\/items\/(\d+)\/line$/, (m, q, b) => editLine(m[1], b)],
    ["POST", /^\/api\/pool\/items\/(\d+)\/pick$/, (m, q, b) => pickItem(m[1], b)],
    ["POST", /^\/api\/pool\/items\/(\d+)\/actual$/, (m, q, b) => setPropActual(m[1], b)],
    ["DELETE", /^\/api\/pool\/items\/(\d+)$/, (m) => deleteItem(m[1])],
    ["GET", /^\/api\/pool\/record$/, (m, q) => poolRecord(Number(q.get("season")))],
    ["GET", /^\/api\/review$/, (m, q) => seasonReview(Number(q.get("season")))],
    ["POST", /^\/api\/lessons\/(\d+)$/, (m, q, b) => {
      const l = S.lessons.find((x) => x.id === Number(m[1]));
      if (!l) err("Lesson not found");
      l.active = b.active ? 1 : 0; save(); return { ok: true };
    }],
    ["DELETE", /^\/api\/lessons\/(\d+)$/, (m) => { S.lessons = S.lessons.filter((x) => x.id !== Number(m[1])); save(); return { ok: true }; }],
    ["GET", /^\/api\/league\/scoring$/, () => scoring()],
    ["POST", /^\/api\/league\/scoring$/, (m, q, b) => { S.scoring = L.merged(b); save(); return S.scoring; }],
    ["POST", /^\/api\/league\/entries$/, (m, q, b) => addEntry(b)],
    ["POST", /^\/api\/league\/entries\/(\d+)$/, (m, q, b) => editEntry(m[1], b)],
    ["DELETE", /^\/api\/league\/entries\/(\d+)$/, (m) => deleteEntry(m[1])],
    ["GET", /^\/api\/league\/week$/, (m, q) => weekView(q)],
    ["GET", /^\/api\/league\/leaderboard$/, (m, q) => leaderboardView(Number(q.get("season")))],
    ["GET", /^\/api\/league\/infer$/, (m, q) => inferView(Number(q.get("season")))],
    ["GET", /^\/api\/league\/highlights$/, () => highlights()],
    ["GET", /^\/api\/league\/owner$/, () => ({ owner: owner() })],
    ["POST", /^\/api\/league\/owner$/, (m, q, b) => {
      const n = String(b.owner || "").trim();
      if (!n) err("Enter a name.");
      S.owner = n; save(); return { owner: n };
    }],
    ["POST", /^\/api\/league\/owner-points\/(\d+)$/, (m, q, b) => {
      const card = cardById(m[1]);
      const v = b.reported_points;
      card.owner_reported_points = v !== null && v !== undefined && v !== "" ? Number(v) : null;
      save(); return { ok: true };
    }],
    ["POST", /^\/api\/league\/highlights$/, (m, q, b) => {
      const clean = {};
      for (const [k, v] of Object.entries(b || {})) if (String(k).trim() && ["red", "gold", "green", "blue"].includes(v)) clean[String(k).trim()] = v;
      S.highlights = clean; save(); return clean;
    }],
    ["GET", /^\/api\/league\/sheet\/week$/, (m, q) => weekSheetView(q)],
    ["GET", /^\/api\/league\/sheet\/season$/, (m, q) => seasonSheetView(q)],
  ];

  async function api(path, opts = {}) {
    const method = (opts.method || "GET").toUpperCase();
    const [p, qs] = path.split("?");
    const q = new URLSearchParams(qs || "");
    for (const [m, re, fn] of routes) {
      if (m !== method) continue;
      const match = re.exec(p);
      if (match) {
        try {
          const out = await fn(match, q, opts.body || {});
          return JSON.parse(JSON.stringify(out === undefined ? null : out));
        } catch (e) {
          if (!(e instanceof ApiError)) console.error(e);
          throw new Error(e.message || String(e));
        }
      }
    }
    throw new Error(`Unknown request: ${method} ${p}`);
  }

  // ---------- backup / restore ----------

  function backup() {
    return JSON.stringify({ app: "cfb-picks", exported_at: nowIso(), state: S }, null, 1);
  }

  function restore(text) {
    let d;
    try { d = JSON.parse(text); } catch (e) { throw new Error("That file isn't a CFB Picks backup."); }
    const st = d && d.state;
    if (!st || typeof st !== "object" || !st.cards) throw new Error("That file isn't a CFB Picks backup.");
    S = Object.assign(blank(), st);
    save();
  }

  root.Store = { api, backup, restore, index, weekData, get state() { return S; } };
})(typeof globalThis !== "undefined" ? globalThis : this);
