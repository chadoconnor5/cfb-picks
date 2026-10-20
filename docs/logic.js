// Pick logic for the browser: a line-for-line port of grading.py, teams.py (names), pool.py, league.py,
// review.py (stats), manual.py and the prompt parts of predict.py. tests/test_js_parity.py checks that
// this file gives the same answers as the Python on the same inputs.
(function (root) {
  "use strict";

  // ---------- Python-compatible helpers ----------

  // round() with Python's ties-to-even on exact halves.
  function pyRound(x, nd = 0) {
    if (x === null || x === undefined || !Number.isFinite(x)) return x;
    const f = 10 ** nd;
    const m = x * f;
    const fl = Math.floor(m);
    if (m - fl === 0.5 && (fl + 0.5) / f === x) return (fl % 2 === 0 ? fl : fl + 1) / f;
    return Number(x.toFixed(nd));
  }

  // Python's format(v, "g") for the values we use (spreads, totals, lines).
  function fmtG(v) {
    if (v === null || v === undefined) return "None";
    let s = Number(v.toPrecision(6)).toString();
    if (s.includes("e")) s = String(v);
    return s;
  }

  const fmtSigned = (v) => (v >= 0 ? "+" : "") + fmtG(v);   // f"{v:+g}"

  // Python's str() of a number inside an f-string (ints stay ints, floats keep ".0").
  function pyStr(v, isFloat) {
    if (v === null || v === undefined) return "None";
    if (typeof v === "boolean") return v ? "True" : "False";
    if (typeof v === "number" && isFloat && Number.isInteger(v)) return v.toFixed(1);
    return String(v);
  }

  // ---------- grading.py ----------

  function gradeAts(side, homeSpread, homePts, awayPts) {
    if ((side !== "home" && side !== "away") || homeSpread == null || homePts == null || awayPts == null) return null;
    const margin = homePts + homeSpread - awayPts;
    if (margin === 0) return "P";
    return (margin > 0) === (side === "home") ? "W" : "L";
  }

  function gradeOu(side, total, homePts, awayPts) {
    if ((side !== "over" && side !== "under") || total == null || homePts == null || awayPts == null) return null;
    const points = homePts + awayPts;
    if (points === total) return "P";
    return (points > total) === (side === "over") ? "W" : "L";
  }

  function formatSpread(team, spread) {
    if (spread === null || spread === undefined) return `${team} (no line)`;
    if (spread === 0) return `${team} PK`;
    return `${team} ${fmtSigned(spread)}`;
  }

  // ---------- teams.py (names) ----------

  function norm(name) {
    let s = (name || "").toLowerCase().replace(/é/g, "e").replace(/'/g, "").replace(/’/g, "");
    s = s.replace(/[^a-z0-9()& ]/g, " ");
    return s.replace(/\s+/g, " ").trim();
  }

  const _aliasCache = new WeakMap();
  function aliasMap(ref) {
    if (_aliasCache.has(ref)) return _aliasCache.get(ref);
    const out = {};
    for (const teams of Object.values(ref.conferences || {})) for (const t of teams) out[norm(t)] = t;
    for (const [canon, alts] of Object.entries(ref.aliases || {})) for (const a of alts) out[norm(a)] = canon;
    _aliasCache.set(ref, out);
    return out;
  }

  function canonical(name, ref) {
    if (!ref || !ref.conferences) return name;
    const m = aliasMap(ref);
    return Object.prototype.hasOwnProperty.call(m, norm(name)) ? m[norm(name)] : name;
  }

  // ---------- difflib (SequenceMatcher.ratio + get_close_matches) ----------

  function matchingBlocksCount(a, b) {
    const b2j = {};
    for (let j = 0; j < b.length; j++) (b2j[b[j]] = b2j[b[j]] || []).push(j);
    function longest(alo, ahi, blo, bhi) {
      let bi = alo, bj = blo, bk = 0;
      let j2len = {};
      for (let i = alo; i < ahi; i++) {
        const newj2len = {};
        for (const j of b2j[a[i]] || []) {
          if (j < blo) continue;
          if (j >= bhi) break;
          const k = (j2len[j - 1] || 0) + 1;
          newj2len[j] = k;
          if (k > bk) { bi = i - k + 1; bj = j - k + 1; bk = k; }
        }
        j2len = newj2len;
      }
      return [bi, bj, bk];
    }
    let total = 0;
    const queue = [[0, a.length, 0, b.length]];
    while (queue.length) {
      const [alo, ahi, blo, bhi] = queue.pop();
      const [i, j, k] = longest(alo, ahi, blo, bhi);
      if (k) {
        total += k;
        if (alo < i && blo < j) queue.push([alo, i, blo, j]);
        if (i + k < ahi && j + k < bhi) queue.push([i + k, ahi, j + k, bhi]);
      }
    }
    return total;
  }

  function ratio(a, b) {
    const t = a.length + b.length;
    return t ? (2 * matchingBlocksCount(a, b)) / t : 1;
  }

  function getCloseMatch(word, possibilities, cutoff = 0.6) {
    let best = null;
    for (const x of possibilities) {
      const r = ratio(x, word);   // SequenceMatcher(None, x, word) with seq2 = word
      if (r >= cutoff && (!best || r > best[0] || (r === best[0] && x > best[1]))) best = [r, x];
    }
    return best ? best[1] : null;
  }

  // ---------- pool.py ----------

  const GAME_RE = /^\s*(?<away>[^@\n]+?)\s*(?:@|\bvs\.?)\s*(?<home>[^(\n]+?)\s*\((?<spread>[+-]?\s*\d+(?:\.\d+)?|PK|pk|Pick)\)\s*(?:o\s*\/\s*u\s*(?<total>\d+(?:\.\d+)?))?\s*(?:(?:mon|tues|wednes|thurs|fri|satur|sun)day\s*)?(?:\*+\s*(?<weight>\d+)\s*-?\s*Point\s+Game\s*\*+)?\s*(?:(?:mon|tues|wednes|thurs|fri|satur|sun)day\s*)?$/i;
  const PICK_RE = /^\s*(?<team>[^,\n]+?)\s*(?:,\s*(?<ou>O|U|Over|Under))?\s*$/i;
  const QUESTION_RE = /^\s*(?<n>\d+)[.)]\s*(?<text>.+)$/;
  const ANSWER_RE = /^\s*(OVER|UNDER|O|U)\s*$/i;
  const TRAIL_ANSWER_RE = /(?<=[?.)])\s*(OVER|UNDER|O|U)\s*$/i;   // "...turnovers? OVER" on one line
  const SHORT_ANSWER_RE = /^(?!.*\?)\S+(?:\s+\S+){0,3}$/;   // "BROWNE", "MISS ST." under a "which ...?" question
  const CHOICES_RE = /\(\s*([^()]+?)\s+or\s+([^()]+?)\s*\)\s*\??\s*$/i;   // "... passing yards? (Lopez or Browne)"
  const LINE_RE = /over\s+or\s+under\s+(?<line>\d+(?:\.\d+)?)\s*(?<unit>[a-z ]*)/i;
  const COMBINED_RE = /\b(both teams|the teams|two teams|teams (?:to )?combine|combined between|total between|between (?:the|both) (?:two )?teams)\b/;
  const TIEBREAK_RE = /combined\s+score\s*[-:]?\s*(?<n>\d+)?/i;

  const STAT_WORDS = [
    ["sacks", ["sack"]],
    ["rush_yds", ["rush", "rushing", "on the ground"]],
    ["pass_yds", ["passing yards", "pass for", "throw for", "passing"]],
    ["total_yds", ["total yards", "yards of offense", "total offense"]],
    ["turnovers", ["turnover"]],
    ["points", ["points", "score"]],
  ];

  function _num(s) {
    s = s.replace(/ /g, "");
    return ["pk", "pick"].includes(s.toLowerCase()) ? 0 : parseFloat(s);
  }

  const startsWithAny = (s, prefixes) => prefixes.some((p) => s.startsWith(p));

  function parseCard(text) {
    const lines = text.split(/\r\n|\r|\n/).map((l) => l.replace(/\s+$/, ""));
    const games = [], props = [];
    let section = "games", tiebreaker = null, hasTiebreaker = false;
    let i = 0;
    while (i < lines.length) {
      const line = lines[i].trim();
      const low = line.toLowerCase();
      if (!line) { i++; continue; }
      if (low.startsWith("questions")) { section = "props"; i++; continue; }
      if (low.includes("mid-major") || low.includes("mid major")) { section = "mid_major"; i++; continue; }
      const tb = TIEBREAK_RE.exec(line);
      if (tb && low.startsWith("combined")) {
        hasTiebreaker = true;
        tiebreaker = tb.groups.n ? parseInt(tb.groups.n, 10) : null;
        if (tiebreaker === null && i + 1 < lines.length && /^\s*\d+\s*$/.test(lines[i + 1])) { tiebreaker = parseInt(lines[i + 1], 10); i++; }
        i++; continue;
      }
      const m = GAME_RE.exec(line);
      if (m) {
        const g = {
          kind: section === "mid_major" ? "mid_major" : "game",
          away: m.groups.away.trim(), home: m.groups.home.trim(),
          home_spread: _num(m.groups.spread),
          total: m.groups.total ? parseFloat(m.groups.total) : null,
          weight: m.groups.weight ? parseInt(m.groups.weight, 10) : 1,
          my_ats_team: null, my_ou: null,
        };
        const nxt = i + 1 < lines.length ? lines[i + 1].trim() : "";
        const pm = nxt && !GAME_RE.test(nxt) && !QUESTION_RE.test(nxt) ? PICK_RE.exec(nxt) : null;
        if (pm && !startsWithAny(nxt.toLowerCase(), ["questions", "mid", "combined"])) {
          g.my_ats_team = pm.groups.team.trim();
          if (pm.groups.ou) g.my_ou = pm.groups.ou.toUpperCase().startsWith("O") ? "over" : "under";
          i++;
        }
        games.push(g);
        i++; continue;
      }
      const q = QUESTION_RE.exec(line);
      if (q && section === "props") {
        const parts = [q.groups.text.trim()];
        let j = i + 1;
        let answer = null;
        while (j < lines.length && lines[j].trim() && !QUESTION_RE.test(lines[j]) && !ANSWER_RE.test(lines[j]) &&
               !startsWithAny(lines[j].trim().toLowerCase(), ["mid", "combined"])) {
          if (/[?)]$/.test(parts[parts.length - 1]) && SHORT_ANSWER_RE.test(lines[j].trim())) { answer = lines[j].trim(); j++; break; }
          parts.push(lines[j].trim());
          j++;
        }
        let qtext = parts.join(" ");
        if (answer !== null) {
          // a "which ...?" answer stays as written; interpretProp matches it to one of the choices
        } else if (j < lines.length && ANSWER_RE.test(lines[j])) {
          answer = lines[j].trim().toUpperCase().startsWith("O") ? "over" : "under";
          j++;
        } else if (TRAIL_ANSWER_RE.test(qtext)) {
          const ta = TRAIL_ANSWER_RE.exec(qtext);
          answer = ta[1].toUpperCase().startsWith("O") ? "over" : "under";
          qtext = qtext.slice(0, ta.index).replace(/\s+$/, "");
        }
        const lm = LINE_RE.exec(qtext);
        const cm = CHOICES_RE.exec(qtext);
        props.push({ kind: "prop", n: parseInt(q.groups.n, 10), question: qtext,
                     line: lm ? parseFloat(lm.groups.line) : null,
                     unit: lm ? (lm.groups.unit || "").trim() : "", my_pick: answer,
                     choices: !lm && cm ? [cm[1].trim(), cm[2].trim()] : null });
        i = j; continue;
      }
      i++;
    }
    return { games, props, tiebreaker, has_tiebreaker: hasTiebreaker };
  }

  const _key = (name, ref) => norm(canonical(name, ref));

  function matchTeam(name, candidates, ref) {
    const k = _key(name, ref);
    const keys = candidates.map((c) => [c, _key(c, ref)]);
    for (const [c, ck] of keys) if (ck === k) return c;
    const abbrevAll = k.split(" st").join(" state").split("st ").join("state ");
    for (const [c, ck] of keys) {
      if (ck === abbrevAll || ck.startsWith(k + " ") || (k.length >= 4 && ck.startsWith(k))) return c;
    }
    const best = getCloseMatch(k, keys.map(([, ck]) => ck), 0.8);
    if (best !== null) return keys.find(([, ck]) => ck === best)[0];
    return null;
  }

  function matchGames(cardGames, weekGames, ref, nick) {
    const names = [...new Set(weekGames.flatMap((g) => [g.home_team, g.away_team]))].sort();
    for (const cg of cardGames) {
      const a = matchTeam(cg.away, names, ref), h = matchTeam(cg.home, names, ref);
      cg.game_id = null; cg.flipped = false;
      for (const g of weekGames) {
        if (g.away_team === a && g.home_team === h) cg.game_id = g.id;
        else if (g.away_team === h && g.home_team === a) { cg.game_id = g.id; cg.flipped = true; }
      }
      cg.cfbd_away = a; cg.cfbd_home = h;
      if (cg.my_ats_team) {
        const t4 = cg.my_ats_team.toLowerCase().slice(0, 4);
        const pick = whichTeam(cg.my_ats_team, [cg.away, cg.home], ref, nick) ||
          (cg.away.toLowerCase().includes(t4) ? cg.away : cg.home.toLowerCase().includes(t4) ? cg.home : null);
        cg.my_ats = pick === cg.home ? "home" : pick === cg.away ? "away" : null;
      }
    }
    return cardGames;
  }

  function interpretProp(prop, weekGames, ref, nick) {
    const low = prop.question.toLowerCase();
    let stat = null;
    for (const [s, words] of STAT_WORDS) if (words.some((w) => low.includes(w))) { stat = s; break; }
    if (stat === null && low.includes("yard")) stat = "total_yds";
    const combined = COMBINED_RE.test(low);
    if (combined && [null, "rush_yds", "pass_yds"].includes(stat) && low.includes("total yards")) stat = "total_yds";
    const found = [];
    for (const g of weekGames) {
      for (const t of [g.home_team, g.away_team]) {
        const canon = canonical(t, ref);
        const variants = new Set([t, canon, ...(((ref || {}).aliases || {})[canon] || [])]);
        const positions = [...variants].map((v) => low.indexOf(v.toLowerCase())).filter((p) => p >= 0);
        const pos = positions.length ? Math.min(...positions) : -1;
        if (pos >= 0) found.push([pos, t, g]);
      }
    }
    found.sort((x, y) => x[0] - y[0]);
    const team = found.length ? found[0][1] : null;
    const game = found.length ? found[0][2] : null;
    let opponent = null;
    if (game) opponent = team === game.home_team ? game.away_team : game.home_team;
    let scope = combined ? "combined" : "team", choices = null, myPick = prop.my_pick;
    // "Which team will have more rushing yards?" / "Which QB ...? (Lopez or Browne)": pick one of two, no line
    if (prop.line == null && (prop.choices || /\b(which|who)\b/.test(low))) {
      const pair = game ? [team, opponent] : null;
      choices = prop.choices || pair;
      if (prop.choices && pair) {
        const m = prop.choices.map((c) => matchTeam(c, pair, ref));
        if (m[0] && m[1] && m[0] !== m[1]) choices = m;
      }
      scope = choices && pair && stat && choices.every((c) => pair.includes(c)) ? "compare" : "choice";
      if (choices && myPick && myPick !== "over" && myPick !== "under") myPick = choiceOf(myPick, choices, ref, nick);
    }
    return { ...prop, team, opponent, game_id: game ? game.id : null, stat, scope, choices, my_pick: myPick === undefined ? null : myPick };
  }

  // A written answer ("MINNESOTA", "Miss St.", "browne") -> one of the question's two choices
  function choiceOf(text, choices, ref, nick) {
    if (!text || !choices) return null;
    const hit = whichTeam(text, choices, ref, nick);
    if (hit) return hit;
    const low = norm(text);
    for (const c of choices) {
      const n = norm(c);
      if (low && (n === low || n.startsWith(low) || low.startsWith(n) || n.split(" ").pop() === low)) return c;
    }
    return null;
  }

  const _fmtSpread = (s) => (s === 0 ? "PK" : fmtSigned(s));

  function exportText(card, picks, props, tiebreaker) {
    const out = [];
    for (const g of card.filter((g) => g.kind === "game")) {
      let line = `${g.away} @ ${g.home} (${_fmtSpread(g.home_spread)})`;
      if (g.total !== null && g.total !== undefined) line += ` o/u ${fmtG(g.total)}`;
      if (g.weight > 1) line += ` *${g.weight} Point Game*`;
      out.push(line);
      const p = picks[g.idx] || {};
      const team = p.ats === "home" ? g.home : p.ats === "away" ? g.away : "?";
      const ou = { over: "O", under: "U" }[p.ou] || "?";
      out.push(g.total !== null && g.total !== undefined ? `${team}, ${ou}` : team);
    }
    if (props.length) {
      out.push("Questions");
      for (const pr of props) {
        out.push(`${pr.n}. ${pr.question}`);
        out.push((pr.pick || "?").toUpperCase());
      }
    }
    const mids = card.filter((g) => g.kind === "mid_major");
    if (mids.length) {
      out.push("");
      out.push("Mid-Major Contest");
      for (const g of mids) {
        out.push(`${g.away} @ ${g.home} (${_fmtSpread(g.home_spread)})`);
        const p = picks[g.idx] || {};
        out.push((p.ats === "home" ? g.home : p.ats === "away" ? g.away : "?").toUpperCase());
      }
    }
    if (tiebreaker !== null && tiebreaker !== undefined) out.push(`Combined Score - ${tiebreaker}`);
    return out.join("\n");
  }

  function gradeGameItem(item, game) {
    if (!game || game.home_points == null || !game.completed) return [null, null];
    let hp = game.home_points, ap = game.away_points;
    if (item.flipped) [hp, ap] = [ap, hp];
    return [gradeAts(item.my_ats, item.home_spread, hp, ap),
            item.total !== null && item.total !== undefined ? gradeOu(item.my_ou, item.total, hp, ap) : null];
  }

  // box: {game_id: {home: {...}, away: {...}}} with team names in game.home_team / away_team
  function propActual(prop, games) {
    if (!prop.stat || !prop.team || prop.game_id == null) return null;
    const g = games[prop.game_id];
    if (!g || !g.box) return null;
    const sideOf = (team) => (team === g.home_team ? "home" : team === g.away_team ? "away" : null);
    const mine = g.box[sideOf(prop.team)];
    if (!mine || mine[prop.stat] == null) return null;
    if (prop.scope === "compare") {   // the answer is the team with more ("tie" if even)
      const other = g.box[sideOf(prop.opponent)];
      if (!other || other[prop.stat] == null) return null;
      return mine[prop.stat] === other[prop.stat] ? "tie" : mine[prop.stat] > other[prop.stat] ? prop.team : prop.opponent;
    }
    if (prop.scope === "choice") return null;
    if (prop.scope === "combined") {
      const other = g.box[sideOf(prop.opponent)];
      if (!other || other[prop.stat] == null) return null;
      return mine[prop.stat] + other[prop.stat];
    }
    return mine[prop.stat];
  }

  function gradeProp(pick, line, actual) {
    if (typeof actual === "string") return pick == null ? null : actual === "tie" ? "P" : pick === actual ? "W" : "L";
    if ((pick !== "over" && pick !== "under") || line == null || actual == null) return null;
    if (actual === line) return "P";
    return (actual > line) === (pick === "over") ? "W" : "L";
  }

  function whichTeam(short, candidates, ref, nick) {
    const hit = matchTeam(short, candidates, ref);
    if (hit) return hit;
    const low = short.toLowerCase().replace(/[^a-z0-9& ]/g, "").trim();
    nick = nick || {};
    for (const name of candidates) {
      if ((nick[name] || []).includes(low) || (nick[canonical(name, ref)] || []).includes(low)) return name;
    }
    for (const name of candidates) {
      const words = name.toLowerCase().replace(/[^a-z ]/g, "").split(/\s+/).filter((w) => w && w !== "of" && w !== "the");
      const initials = words.map((w) => w[0]).join("");
      if (words.length > 1 && [initials, "u" + initials, initials + "u"].includes(low)) return name;
    }
    for (const name of candidates) {
      const words = name.toLowerCase().split(/\s+/).filter(Boolean), parts = low.split(/\s+/).filter(Boolean);
      if (parts.length && parts.length <= words.length && parts.every((p, k) => words[k].startsWith(p))) return name;
    }
    return null;
  }

  function parseEntry(text, cardItems, ref, nick) {
    const parsed = parseCard(text);
    const gameItems = cardItems.filter((it) => it.kind === "game" || it.kind === "mid_major");
    const propItems = {};
    for (const it of cardItems) if (it.kind === "prop") propItems[it.data.n] = it;
    const picks = {}, warnings = [], used = new Set();
    parsed.games.forEach((g, pos) => {
      let target = null;
      for (const it of gameItems) {
        const d = it.data;
        if (used.has(it.idx)) continue;
        if (matchTeam(g.away, [d.away], ref) && matchTeam(g.home, [d.home], ref)) { target = it; break; }
      }
      if (target === null && pos < gameItems.length && !used.has(gameItems[pos].idx)) {
        target = gameItems[pos];
        warnings.push(`Matched '${g.away} @ ${g.home}' by position on the card`);
      }
      if (target === null) { warnings.push(`Couldn't match game '${g.away} @ ${g.home}'`); return; }
      used.add(target.idx);
      const d = target.data;
      let side = null;
      if (g.my_ats_team) {
        const t = g.my_ats_team;
        const hit = whichTeam(t, [d.away, d.home], ref, nick);
        side = hit === d.home ? "home" : hit === d.away ? "away" : null;
        if (side === null) warnings.push(`Couldn't tell which team '${t}' is in ${d.away} @ ${d.home}`);
      } else if (g.my_ou === null) {
        warnings.push(`No pick found for ${d.away} @ ${d.home}`);
      }
      picks[String(target.idx)] = { ats: side, ou: g.my_ou };
    });
    for (const pr of parsed.props) {
      const it = propItems[pr.n];
      if (!it) { warnings.push(`Question ${pr.n} isn't on this week's card`); continue; }
      if (pr.my_pick === null) warnings.push(`No answer found for question ${pr.n}`);
      picks[String(it.idx)] = { pick: pr.my_pick };
    }
    for (const it of gameItems) if (!used.has(it.idx)) warnings.push(`No pick for ${it.data.away} @ ${it.data.home}`);
    if (parsed.has_tiebreaker && parsed.tiebreaker === null) warnings.push("No combined-score guess found");
    return { picks, tiebreaker: parsed.tiebreaker, warnings };
  }

  // ---------- league.py ----------

  const DEFAULT_SCORING = {
    ats: 3, ou: 1, prop: 1, mid_major: 2,
    bonus_game_mode: "ats",
    tiebreak: { type: "none" },
    potw_bonus: 3,
    potw_ties: "all",
  };

  function merged(config) {
    const out = JSON.parse(JSON.stringify(DEFAULT_SCORING));
    for (const [k, v] of Object.entries(config || {})) out[k] = v;
    return out;
  }

  function actualCombined(items) {
    const games = items.filter((it) => it.kind === "game");
    if (!games.length || games.some((it) => !(it.game || {}).completed)) return null;
    return games.reduce((s, it) => s + it.game.home_points + it.game.away_points, 0);
  }

  function _gameResult(item, side, totalPick) {
    const g = item.game || {};
    if (!g.completed || g.home_points == null) return [null, null];
    const d = item.data;
    let hp = g.home_points, ap = g.away_points;
    if (d.flipped) [hp, ap] = [ap, hp];
    const ats = side ? gradeAts(side, d.home_spread, hp, ap) : null;
    const ou = totalPick && d.total != null ? gradeOu(totalPick, d.total, hp, ap) : null;
    return [ats, ou];
  }

  function _propResult(item, pick) {
    const actual = item.prop_actual, line = item.data.line;
    if (typeof actual === "string") return pick == null ? null : actual === "tie" ? "P" : pick === actual ? "W" : "L";
    if ((pick !== "over" && pick !== "under") || actual == null || line == null) return null;
    if (actual === line) return "P";
    return (actual > line) === (pick === "over") ? "W" : "L";
  }

  function scoreEntry(items, picks, cfg) {
    const b = { ats: 0, ou: 0, prop: 0, mid_major: 0, bonus_game: 0, correct: 0, graded: 0 };
    const detail = [];
    for (const it of items) {
      const p = picks[String(it.idx)] || picks[it.idx] || {};
      const d = it.data;
      if (it.kind === "prop") {
        const r = _propResult(it, p.pick);
        const pts = r === "W" ? cfg.prop : 0;
        b.prop += pts;
        detail.push({ idx: it.idx, kind: "prop", pick: p.pick === undefined ? null : p.pick, result: r, points: pts });
      } else {
        const [ats, ou] = _gameResult(it, p.ats, p.ou);
        const w = d.weight === undefined ? 1 : d.weight;
        let atsPts, ouPts;
        if (it.kind === "mid_major") {
          atsPts = ats === "W" ? cfg.mid_major : 0;
          b.mid_major += atsPts;
          ouPts = 0;
        } else {
          let atsVal, ouVal;
          if (w > 1 && cfg.bonus_game_mode === "ats") [atsVal, ouVal] = [w, cfg.ou];
          else if (w > 1 && cfg.bonus_game_mode === "both") [atsVal, ouVal] = [w, w];
          else if (w > 1 && cfg.bonus_game_mode === "ats_plus") [atsVal, ouVal] = [cfg.ats + w, cfg.ou];
          else [atsVal, ouVal] = [cfg.ats, cfg.ou];
          atsPts = ats === "W" ? atsVal : 0;
          ouPts = ou === "W" ? ouVal : 0;
          if (w > 1) b.bonus_game += atsPts + ouPts;
          else { b.ats += atsPts; b.ou += ouPts; }
        }
        detail.push({ idx: it.idx, kind: it.kind, ats: p.ats === undefined ? null : p.ats, ou: p.ou === undefined ? null : p.ou,
                      ats_result: ats, ou_result: ou, points: atsPts + ouPts });
      }
      const last = detail[detail.length - 1];
      const rs = it.kind === "prop" ? [last.result] : [last.ats_result, last.ou_result];
      for (const r of rs) {
        if (r === "W" || r === "L" || r === "P") { b.graded += 1; b.correct += r === "W" ? 1 : 0; }
      }
    }
    const base = b.ats + b.ou + b.prop + b.mid_major + b.bonus_game;
    return { base, breakdown: b, detail };
  }

  function tiebreakPoints(rule, guesses, actual) {
    const out = {};
    for (const p of Object.keys(guesses)) out[p] = 0;
    if (actual == null || !rule || rule.type == null || rule.type === "none") return out;
    const dist = {};
    for (const [p, g] of Object.entries(guesses)) if (g != null) dist[p] = Math.abs(g - actual);
    const vals = Object.values(dist);
    if (!vals.length) return out;
    const t = rule.type;
    const pts = rule.points === undefined ? 1 : rule.points;
    if (t === "closest") {
      const best = Math.min(...vals);
      for (const [p, d] of Object.entries(dist)) if (d === best) out[p] = pts;
    } else if (t === "exact") {
      for (const [p, d] of Object.entries(dist)) if (d === 0) out[p] = pts;
    } else if (t === "within") {
      const margin = rule.margin === undefined ? 5 : rule.margin;
      for (const [p, d] of Object.entries(dist)) if (d <= margin) out[p] = pts;
    } else if (t === "tiers") {
      const ranked = [...new Set(vals)].sort((a, b) => a - b);
      const tiers = rule.points || [];
      for (const [p, d] of Object.entries(dist)) {
        const r = ranked.indexOf(d);
        out[p] = r < tiers.length ? tiers[r] : 0;
      }
    }
    return out;
  }

  function weekComplete(items) {
    if (!items.length) return false;
    for (const it of items) {
      if (it.kind === "prop") { if (it.prop_actual == null) return false; }
      else if (!(it.game || {}).completed) return false;
    }
    return true;
  }

  function scoreWeek(items, entries, cfg) {
    cfg = merged(cfg);
    const actual = actualCombined(items);
    const rows = entries.map((e) => {
      const s = scoreEntry(items, e.picks, cfg);
      return { player: e.player, tiebreaker: e.tiebreaker === undefined ? null : e.tiebreaker, ...s,
               distance: actual != null && e.tiebreaker != null ? Math.abs(e.tiebreaker - actual) : null };
    });
    const guesses = {};
    for (const r of rows) guesses[r.player] = r.tiebreaker;
    const tb = tiebreakPoints(cfg.tiebreak, guesses, actual);
    for (const r of rows) { r.tiebreak_points = tb[r.player] || 0; r.pre_bonus = r.base + r.tiebreak_points; }
    const allGraded = rows.length > 0 && weekComplete(items);
    for (const r of rows) r.potw = false;
    if (rows.length && allGraded) {
      const top = Math.max(...rows.map((r) => r.pre_bonus));
      let leaders = rows.filter((r) => r.pre_bonus === top);
      if (leaders.length > 1 && cfg.potw_ties === "closest_combined" && actual != null) {
        const ds = leaders.map((r) => r.distance).filter((d) => d != null);
        const best = ds.length ? Math.min(...ds) : null;
        if (best != null) leaders = leaders.filter((r) => r.distance === best);
      }
      for (const r of leaders) r.potw = true;
    }
    for (const r of rows) { r.potw_points = r.potw ? cfg.potw_bonus : 0; r.total = r.pre_bonus + r.potw_points; }
    rows.sort((a, b) => (b.total - a.total) || ((a.distance != null ? a.distance : 9e9) - (b.distance != null ? b.distance : 9e9)));
    return { actual_combined: actual, complete: Boolean(allGraded), rows };
  }

  function leaderboard(weeks) {
    const table = {};
    const order = [];
    for (const w of weeks) {
      for (const r of w.rows) {
        if (!table[r.player]) {
          table[r.player] = { player: r.player, total: 0, weeks: 0, potw: 0, correct: 0, graded: 0, by_week: {}, ats: 0, ou: 0,
                              prop: 0, mid_major: 0, bonus_game: 0, tiebreak: 0 };
          order.push(r.player);
        }
        const t = table[r.player];
        t.total += r.total; t.weeks += 1; t.potw += r.potw ? 1 : 0;
        t.correct += r.breakdown.correct; t.graded += r.breakdown.graded;
        for (const k of ["ats", "ou", "prop", "mid_major", "bonus_game"]) t[k] += r.breakdown[k];
        t.tiebreak += r.tiebreak_points;
        t.by_week[w.week] = r.total;
      }
    }
    const out = order.map((p) => table[p]).sort((a, b) => b.total - a.total);
    for (const t of out) {
      t.rank = 1 + out.filter((o) => o.total > t.total).length;
      t.avg = t.weeks ? pyRound(t.total / t.weeks, 2) : null;
      t.pick_pct = t.graded ? pyRound((100 * t.correct) / t.graded, 1) : null;
    }
    return out;
  }

  function* candidateConfigs(baseCfg) {
    const tiebreaks = [{ type: "none" }];
    for (const pts of [1, 2, 3, 5]) tiebreaks.push({ type: "closest", points: pts }, { type: "exact", points: pts });
    for (const tiers of [[3, 2, 1], [2, 1], [5, 3, 1], [3, 1]]) tiebreaks.push({ type: "tiers", points: tiers });
    for (const pts of [1, 2, 3]) for (const margin of [5, 10]) tiebreaks.push({ type: "within", points: pts, margin });
    for (const mode of ["ats", "both", "ats_plus"]) for (const tb of tiebreaks) for (const ties of ["all", "closest_combined"]) {
      yield merged({ ...baseCfg, bonus_game_mode: mode, tiebreak: tb, potw_ties: ties });
    }
  }

  function describeRule(cfg) {
    const tb = cfg.tiebreak;
    const pts = Array.isArray(tb.points) ? `[${tb.points.join(", ")}]` : tb.points;
    const t = { none: "no points for the combined score", closest: `closest combined-score guess gets ${pts}`,
                exact: `exact combined-score guess gets ${pts}`, within: `guesses within ${tb.margin} get ${pts}`,
                tiers: `closest guesses get ${pts} by rank` }[tb.type];
    const b = { ats: "N-Point Game: spread pick worth N", both: "N-Point Game: spread and O/U both worth N",
                ats_plus: "N-Point Game: spread pick worth 3 + N" }[cfg.bonus_game_mode];
    const p = cfg.potw_ties === "all" ? "Player of the Week ties: all tied get the bonus" : "Player of the Week ties: broken by combined-score guess";
    return `${t}; ${b}; ${p}`;
  }

  function inferRules(weeks, baseCfg) {
    const usable = weeks.filter((w) => w.entries.some((e) => e.reported_points != null));
    if (!usable.length) return { message: "Enter the league's posted weekly points for some players to infer the rules.", candidates: [] };
    const results = [];
    for (const cfg of candidateConfigs(baseCfg)) {
      let exact = 0, total = 0, absErr = 0;
      for (const w of usable) {
        const scored = {};
        for (const r of scoreWeek(w.items, w.entries, cfg).rows) scored[r.player] = r;
        for (const e of w.entries) {
          if (e.reported_points == null || !scored[e.player]) continue;
          const diff = scored[e.player].total - e.reported_points;
          total += 1; exact += diff === 0 ? 1 : 0; absErr += Math.abs(diff);
        }
      }
      if (total) results.push({ config: cfg, rule: describeRule(cfg), exact, of: total,
                                match_pct: pyRound((100 * exact) / total, 1), avg_abs_error: pyRound(absErr / total, 2) });
    }
    results.sort((a, b) => (b.exact - a.exact) || (a.avg_abs_error - b.avg_abs_error));
    const residuals = [];
    for (const w of usable) {
      const scored = {};
      for (const r of scoreWeek(w.items, w.entries, baseCfg).rows) scored[r.player] = r;
      for (const e of w.entries) {
        if (e.reported_points != null && scored[e.player]) {
          const s = scored[e.player];
          residuals.push({ week: w.week, player: e.player, reported: e.reported_points, computed: s.total,
                           unexplained: e.reported_points - s.total, guess: e.tiebreaker === undefined ? null : e.tiebreaker,
                           distance: s.distance, potw: s.potw });
        }
      }
    }
    const best = results.length ? results[0] : null;
    let msg = null;
    if (best && best.exact === 0) {
      msg = `None of the ${results.length} rule sets reproduces any posted score. The league may use a rule not modeled ` +
        "here, or some posted points/picks were entered wrong. Check 'Unexplained points' below: a constant gap " +
        "points to a missing per-week rule, a gap only for some players points to the combined-score or POTW rule.";
    } else if (best) {
      const ties = results.filter((r) => r.exact === best.exact && r.avg_abs_error === best.avg_abs_error);
      msg = `Best fit reproduces ${best.exact} of ${best.of} posted scores (${best.match_pct}%).` +
        (ties.length > 1 ? ` ${ties.length} rule sets fit equally well; more weeks will narrow it down.` : "");
    }
    return { message: msg, candidates: results.slice(0, 10), residuals };
  }

  const DEFAULT_HIGHLIGHTS = { "Patrick Kenney": "red", "Zach Kenney": "red", "Kevin Kenney": "red", "Darren Kenney": "red", "Ron O'Connor": "gold" };

  function normName(name) {
    return (name || "").replace(/’/g, "'").replace(/‘/g, "'").replace(/`/g, "'").toLowerCase().split(/\s+/).filter(Boolean).join(" ");
  }

  function namesMatch(a, b) {
    const x = normName(a), y = normName(b);
    if (x === y) return true;
    const px = x.replace(/\./g, " ").split(/\s+/).filter(Boolean), py = y.replace(/\./g, " ").split(/\s+/).filter(Boolean);
    if (px.length < 2 || py.length < 2 || px.slice(1).join(" ") !== py.slice(1).join(" ")) return false;
    return (px[0].length === 1 || py[0].length === 1) && px[0][0] === py[0][0];
  }

  function highlightFor(name, highlights) {
    for (const [k, v] of Object.entries(highlights || {})) if (namesMatch(k, name)) return v;
    return null;
  }

  // The league's weekly spreadsheet (tab-separated, as copied from Sheets/Excel): a row of games like
  // "Colo @ GT (-6.5)" with the O/U in the next column, Q1..Qn columns, a mid-major game, then one row
  // per player. Returns the games (with their column), the question columns and each player's cells.
  function parseLeagueSheet(text) {
    const rows = text.split(/\r\n|\r|\n/).map((r) => r.split("\t").map((c) => c.trim()));
    const GAME = /^(?<away>.+?)\s*(?:@|\bvs\.?)\s*(?<home>[^()]+?)\s*\((?<spread>[+-]?\s*\d+(?:\.\d+)?|PK|pk|Pick)\)\s*$/i;
    const gi = rows.findIndex((r) => r.filter((c) => GAME.test(c)).length >= 2);
    if (gi < 0) return null;
    const head = rows[gi];
    const above = rows.slice(0, gi);
    const colText = (i) => above.map((r) => r[i] || "").join(" ");
    const games = [], props = [];
    let midSeen = false, tiebreakCol = null;
    head.forEach((c, i) => {
      if (/combined\s+score/i.test(c)) { tiebreakCol = i; return; }
      const q = /^Q(\d+)$/i.exec(c);
      if (q) { props.push({ n: parseInt(q[1], 10), col: i }); return; }
      const m = GAME.exec(c);
      if (!m) return;
      const nextNum = head[i + 1] !== undefined && /^\d+(\.\d+)?$/.test(head[i + 1]) ? parseFloat(head[i + 1]) : null;
      const isMid = /mid[- ]?major/i.test(colText(i)) || (nextNum === null && props.length > 0) || midSeen;
      if (isMid) midSeen = true;
      const w = /(\d+)\s*-?\s*Point\s+Game/i.exec(colText(i));
      const sp = m.groups.spread.replace(/ /g, "");
      games.push({ kind: isMid ? "mid_major" : "game", away: m.groups.away.trim(), home: m.groups.home.trim(),
                   home_spread: /^(pk|pick)$/i.test(sp) ? 0 : parseFloat(sp), total: isMid ? null : nextNum,
                   weight: w ? parseInt(w[1], 10) : 1, col: i });
    });
    const players = [];
    for (const r of rows.slice(gi + 1)) {
      const name = (r[0] || "").trim();
      if (!name || r.slice(1).every((c) => !c)) continue;
      players.push({ name, cells: r });
    }
    return { games, props, players, tiebreak_col: tiebreakCol };
  }

  function pickLabel(item, pick) {
    if (!pick) return "";
    if (item.kind === "prop") return (pick.pick || "").toUpperCase();
    const d = item.data;
    const team = pick.ats === "home" ? d.home : pick.ats === "away" ? d.away : "?";
    const ou = { over: "O", under: "U" }[pick.ou];
    return item.kind === "game" ? `${team}${ou ? ", " + ou : ""}` : team;
  }

  function weekSheet(items, scored, entries, highlights) {
    const byPlayer = {};
    for (const e of entries) byPlayer[e.player] = e;
    const cols = items.map((it) => ({
      idx: it.idx, kind: it.kind, weight: it.data.weight === undefined ? 1 : it.data.weight,
      label: it.kind === "prop" ? `Q${it.data.n}` : `${it.data.away} @ ${it.data.home}`,
      line: it.kind === "prop" ? (it.data.choices ? it.data.choices.join(" or ") : it.data.line != null ? `O/U ${pyStr(it.data.line, true)}` : "line not set") :
        `${it.data.home} ${fmtSigned(it.data.home_spread)}` + (it.data.total != null ? `, o/u ${fmtG(it.data.total)}` : ""),
    }));
    const rows = scored.rows.map((r) => {
      const picks = (byPlayer[r.player] || {}).picks || {};
      const det = {};
      for (const d of r.detail) det[d.idx] = d;
      const cells = items.map((it) => {
        const d = det[it.idx] || {};
        const res = it.kind === "prop" ? d.result : d.ats_result;
        return { label: pickLabel(it, picks[String(it.idx)] || picks[it.idx]), ats_result: res === undefined ? null : res,
                 ou_result: d.ou_result === undefined ? null : d.ou_result, points: d.points || 0 };
      });
      return { player: r.player, highlight: highlightFor(r.player, highlights), cells, tiebreaker: r.tiebreaker,
               distance: r.distance, breakdown: r.breakdown, tiebreak_points: r.tiebreak_points, potw: r.potw,
               potw_points: r.potw_points, total: r.total, reported_points: r.reported_points === undefined ? null : r.reported_points };
    });
    return { columns: cols, rows };
  }

  function seasonSheet(standings, weeks, highlights) {
    const leader = standings.length ? standings[0].total : 0;
    const weekCols = weeks.map((w) => w.week);
    const rows = standings.map((s) => ({ ...s, highlight: highlightFor(s.player, highlights), behind: leader - s.total,
                                         weeks_cells: weekCols.map((w) => (s.by_week[w] === undefined ? null : s.by_week[w])) }));
    return { weeks: weekCols, rows };
  }

  function toCsv(header, rows) {
    const cell = (v) => {
      const s = v === null || v === undefined ? "" : String(v);
      return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    return [header, ...rows].map((r) => r.map(cell).join(",")).join("\r\n") + "\r\n";
  }

  // ---------- review.py (deterministic stats) ----------

  const BUCKETS = [[50, 54], [55, 59], [60, 64], [65, 69], [70, 95]];

  function _rec(results) {
    const w = results.filter((r) => r === "W").length, l = results.filter((r) => r === "L").length,
      p = results.filter((r) => r === "P").length;
    return { w, l, p, pct: w + l ? pyRound((100 * w) / (w + l), 1) : null };
  }

  function weekItems(items) {
    const rows = [];
    for (const it of items) {
      const c = it.claude || {}, d = it.data;
      if (!it.claude) continue;
      const g = it.game || {};
      if (it.kind === "prop") {
        rows.push({ idx: it.idx, type: "prop", pick: nz(c.pick), confidence: nz(c.confidence), result: nz(it.claude_prop_result),
                    mine: nz(it.prop_result), line: nz(d.line), actual: nz(it.prop_actual), question: nz(d.question),
                    projection: nz(c.projection), reasoning: nz(c.reasoning), audit: nz(c.audit) });
        continue;
      }
      const base = { idx: it.idx, game: `${d.away} @ ${d.home}`, weight: d.weight === undefined ? 1 : d.weight,
                     pool_spread: d.home_spread, pool_total: nz(d.total), confidence: nz(c.confidence),
                     proj: `${d.away} ${pyStr(nz(c.proj_away))}-${pyStr(nz(c.proj_home))} ${d.home}`,
                     final: g.completed ? `${d.away} ${pyStr(nz(g.away_points))}-${pyStr(nz(g.home_points))} ${d.home}` : null,
                     reasoning: nz(c.reasoning), key_factors: nz(c.key_factors), conditions_impact: nz(c.conditions_impact),
                     audit: nz(c.audit), market: nz(it.market), actual: nz(d.actual), flipped: nz(d.flipped) };
      if (c.ats_pick) rows.push({ ...base, type: it.kind === "mid_major" ? "mid_major" : "ats", pick: c.ats_pick,
                                  result: nz(it.claude_ats_result), mine: nz(it.ats_result), my_pick: nz(it.my_ats) });
      if (c.ou_pick) rows.push({ ...base, type: "ou", pick: c.ou_pick, result: nz(it.claude_ou_result), mine: nz(it.ou_result),
                                 my_pick: nz(it.my_ou) });
    }
    return rows;
  }

  function nz(v) { return v === undefined ? null : v; }

  function _clv(row) {
    const m = row.market || {};
    if ((row.type === "ats" || row.type === "mid_major") && m.home_spread != null) {
      const diff = row.pool_spread - m.home_spread;
      return row.pick === "home" ? diff : -diff;
    }
    if (row.type === "ou" && m.total != null && row.pool_total != null) {
      const diff = m.total - row.pool_total;
      return row.pick === "over" ? diff : -diff;
    }
    return null;
  }

  function stats(rows) {
    const graded = rows.filter((r) => ["W", "L", "P"].includes(r.result));
    const out = { n: graded.length, overall: _rec(graded.map((r) => r.result)) };
    const byType = {};
    for (const r of graded) (byType[r.type] = byType[r.type] || []).push(r.result);
    out.by_type = {};
    for (const [k, v] of Object.entries(byType)) out.by_type[k] = _rec(v);
    out.calibration = [];
    for (const [lo, hi] of BUCKETS) {
      const sub = graded.filter((r) => r.confidence != null && lo <= r.confidence && r.confidence <= hi);
      if (sub.length) out.calibration.push({ range: `${lo}-${hi}`, expected: pyRound((lo + hi) / 2), ..._rec(sub.map((r) => r.result)) });
    }
    const ats = graded.filter((r) => r.type === "ats" || r.type === "mid_major");
    const fav = ats.filter((r) => (r.pick === "home") === (r.pool_spread < 0));
    const dog = ats.filter((r) => (r.pick === "home") === (r.pool_spread > 0));
    out.favorites = _rec(fav.map((r) => r.result));
    out.underdogs = _rec(dog.map((r) => r.result));
    const clv = graded.map((r) => [r, _clv(r)]).filter(([, v]) => v != null);
    if (clv.length) {
      out.closing_line_value = {
        avg_points: pyRound(clv.reduce((s, [, v]) => s + v, 0) / clv.length, 2),
        beat_close_pct: pyRound((100 * clv.filter(([, v]) => v > 0).length) / clv.length, 1),
        with_market_move: _rec(clv.filter(([, v]) => v > 0).map(([r]) => r.result)),
        against_market_move: _rec(clv.filter(([, v]) => v < 0).map(([r]) => r.result)),
      };
    }
    const mine = graded.filter((r) => ["W", "L", "P"].includes(r.mine));
    out.me = _rec(mine.map((r) => r.mine));
    const agree = mine.filter((r) => r.my_pick && r.my_pick === r.pick);
    const dis = mine.filter((r) => r.my_pick && r.my_pick !== r.pick);
    out.agreement = { n: agree.length, of: mine.filter((r) => r.my_pick).length, when_agree: _rec(agree.map((r) => r.result)),
                      claude_when_disagree: _rec(dis.map((r) => r.result)), me_when_disagree: _rec(dis.map((r) => r.mine)) };
    return out;
  }

  function calibrationLines(seasonStats) {
    const lines = [];
    for (const b of seasonStats.calibration || []) {
      if (b.w + b.l >= 5) {
        const verdict = b.pct !== null && b.pct < b.expected - 8 ? "you are overconfident there" :
          b.pct !== null && Math.abs(b.pct - b.expected) <= 8 ? "roughly calibrated" : "you can be more confident there";
        lines.push(`Your ${b.range} confidence picks have won ${pyStr(b.pct, true)}% (${b.w}-${b.l}); ${verdict}.`);
      }
    }
    const clv = seasonStats.closing_line_value;
    if (clv) {
      const avg = (clv.avg_points >= 0 ? "+" : "") + pyStr(clv.avg_points, true);
      lines.push(`You beat the closing line on ${pyStr(clv.beat_close_pct, true)}% of picks (avg ${avg} pts); ` +
        `picks with the market move went ${clv.with_market_move.w}-${clv.with_market_move.l}, ` +
        `against it ${clv.against_market_move.w}-${clv.against_market_move.l}.`);
    }
    for (const [k, label] of [["ats", "spread"], ["ou", "over/under"], ["prop", "prop"]]) {
      const r = (seasonStats.by_type || {})[k];
      if (r && r.w + r.l >= 5) lines.push(`Season ${label} record: ${r.w}-${r.l} (${pyStr(r.pct, true)}%).`);
    }
    return lines;
  }

  // ---------- predict.py (prompt pieces + answer validation) ----------

  function headerLines(game, market) {
    const home = game.home_team, away = game.away_team;
    const spread = game.spread, total = game.total;
    const site = game.neutral_site ? "Neutral site" : `at ${home}`;
    const lines = [
      `Matchup: ${away} (${game.away_conf || "?"}) vs ${home} (${game.home_conf || "?"})`,
      `Season ${game.season}, week ${game.week} (${game.season_type}). ${site}` + (game.venue ? `, ${game.venue}` : "") + ".",
      `Kickoff: ${game.start_date || "TBD"}`,
      `Spread: ${formatSpread(home, spread)} / ${formatSpread(away, spread != null ? -spread : null)}`,
      `Total: ${total != null ? pyStr(total, true) : "no line"}`,
    ];
    if (market) {
      const label = market.label || "current sportsbook line";
      lines.push(`This is a pick'em pool line set earlier in the week. Market (${label}): ` +
        `${formatSpread(market.home, market.spread)}, total ${market.total != null ? pyStr(market.total, true) : "n/a"}. ` +
        "Pick against the pool line above; use any gap to the market as information.");
    }
    if (game.spread_open != null || game.total_open != null) {
      lines.push(`Opening line: ${formatSpread(home, game.spread_open)}, total ${pyStr(game.total_open, true)}`);
    }
    return lines;
  }

  function rowFromData(game, data, model) {
    const missing = ["ats_pick", "ou_pick", "projected_home_score", "projected_away_score", "confidence", "reasoning"].filter((k) => !(k in data));
    if (missing.length) throw new Error(`Answer is missing: ${missing.join(", ")}`);
    const pick = String(data.ats_pick).trim();
    let side;
    if ([game.home_team.toLowerCase(), "home"].includes(pick.toLowerCase())) side = "home";
    else if ([game.away_team.toLowerCase(), "away"].includes(pick.toLowerCase())) side = "away";
    else throw new Error(`ats_pick '${pick}' is neither ${game.away_team} nor ${game.home_team}`);
    const ou = data.ou_pick ? String(data.ou_pick).trim().toLowerCase() : null;
    if (![null, "over", "under"].includes(ou)) throw new Error(`ou_pick must be over or under, got '${data.ou_pick}'`);
    const num = (v, name) => {
      const n = Number(v);
      if (v === null || v === "" || !Number.isFinite(n)) throw new Error(`${name} must be a number`);
      return n;
    };
    return {
      ats_pick: side, ou_pick: ou,
      proj_home: pyRound(num(data.projected_home_score, "projected_home_score")),
      proj_away: pyRound(num(data.projected_away_score, "projected_away_score")),
      confidence: Math.max(50, Math.min(95, pyRound(num(data.confidence, "confidence")))),
      reasoning: String(data.reasoning).trim(),
      key_factors: (data.key_factors || []).map((f) => String(f).trim()).filter(Boolean).slice(0, 3),
      conditions_impact: String(data.conditions_impact || "").trim(),
      model: model || "claude",
    };
  }

  function propFromData(data, model) {
    const pick = String(data.pick == null ? "" : data.pick).trim().toLowerCase();
    if (pick !== "over" && pick !== "under") throw new Error(`pick must be over or under, got '${data.pick}'`);
    const conf = Number(data.confidence == null ? 55 : data.confidence);
    if (!Number.isFinite(conf)) throw new Error("confidence must be a number");
    return { pick, projection: data.projection != null && Number.isFinite(Number(data.projection)) ? Number(data.projection) : null,
             confidence: Math.max(50, Math.min(95, pyRound(conf))), reasoning: String(data.reasoning || "").trim(), model: model || "claude" };
  }

  function propPrompt(prop, game, propStats, assessmentText, statLabels) {
    const labels = statLabels || {};
    const lines = [`Question: ${prop.question}`, `Line: ${pyStr(prop.line, true)}`,
      `Interpreted as: ${labels[prop.stat] || prop.stat || "unknown stat"} - ` +
      (prop.scope === "combined" ? "both teams combined" : `${prop.team} only`) + (prop.opponent ? ` (opponent: ${prop.opponent})` : "")];
    if (game) {
      lines.push(`Game: ${game.away_team} at ${game.home_team}, line ${formatSpread(game.home_team, game.spread)}, ` +
        `total ${pyStr(game.total, true)}, kickoff ${game.start_date}`);
    }
    if (propStats) {
      const key = prop.stat;
      for (const side of ["away", "home"]) {
        const t = propStats[side];
        if (!t) continue;
        if (key) {
          const f = t.for[key], a = t.against[key];
          lines.push(`${t.team} ${labels[key] || key} per game this season: ` +
            (f ? `avg ${f.avg}, last 3 ${f.last3}, range ${f.min}-${f.max} (n=${f.n})` : "n/a") + "; allowed to opponents: " +
            (a ? `avg ${a.avg}, last 3 ${a.last3}` : "n/a"));
        }
        lines.push(`${t.team} game log: ` + t.games.slice(-6).map((g) => `vs ${g.opponent}: ${key ? (g[key] == null ? "None" : g[key]) : ""}`).join("; "));
      }
    }
    if (assessmentText) lines.push("Weather/style:", assessmentText);
    return lines.join("\n");
  }

  // ---------- manual.py ----------

  function extractJson(text) {
    if (!text || !text.trim()) throw new Error("Paste Claude's answer first.");
    const t = text.trim();
    const fence = /```(?:json)?\s*([\s\S]*?)```/.exec(t);
    const candidates = (fence ? [fence[1]] : []).concat([t]);
    for (let c of candidates) {
      c = c.replace(/“/g, '"').replace(/”/g, '"');
      for (let i = 0; i < c.length; i++) {
        if (c[i] !== "[" && c[i] !== "{") continue;
        const end = matchingClose(c, i);
        if (end < 0) continue;
        try { return JSON.parse(c.slice(i, end + 1)); } catch (e) { /* keep looking */ }
      }
    }
    throw new Error("Couldn't find JSON in the answer. Copy Claude's whole reply (or just the JSON block).");
  }

  function matchingClose(s, start) {   // index of the bracket closing s[start], respecting strings
    const stack = [];
    let inStr = false, esc = false;
    for (let i = start; i < s.length; i++) {
      const ch = s[i];
      if (inStr) {
        if (esc) esc = false;
        else if (ch === "\\") esc = true;
        else if (ch === '"') inStr = false;
        continue;
      }
      if (ch === '"') inStr = true;
      else if (ch === "[" || ch === "{") stack.push(ch);
      else if (ch === "]" || ch === "}") {
        const open = stack.pop();
        if ((open === "[" && ch !== "]") || (open === "{" && ch !== "}")) return -1;
        if (!stack.length) return i;
      }
    }
    return -1;
  }

  function answersByItem(data) {
    if (data && !Array.isArray(data) && typeof data === "object" && Array.isArray(data.items)) data = data.items;
    if (data && !Array.isArray(data) && typeof data === "object") data = [data];
    if (!Array.isArray(data)) throw new Error("Expected a JSON array of answers.");
    const out = new Map();
    data.forEach((obj, k) => {
      const i = k + 1;
      if (!obj || typeof obj !== "object" || Array.isArray(obj)) throw new Error(`Answer #${i} isn't a JSON object.`);
      let n = obj.item !== undefined ? obj.item : obj.idx;
      if (n === undefined || n === null) n = data.length > 1 ? i : null;
      else {
        const v = Number(n);
        if (!Number.isInteger(v)) throw new Error(`Answer #${i} has a bad item number: ${JSON.stringify(n)}`);
        n = v;
      }
      out.set(n, obj);
    });
    return out;
  }

  function researchBlock(teams, asOf, replay) {
    const uniq = [...new Set(teams)].sort();
    const lines = ["RESEARCH FIRST (use web search):",
      `- Injury / availability news for: ${uniq.join(", ")} (QB, skill players, OL, key defenders; ` +
        "practice reports, coach comments, opt-outs, suspensions).",
      "- Coaching news for those programs: staff changes, interim coaches, hot-seat talk, rumors, " +
        "and anything that changes motivation.",
      "- Anything else the data below can't know (QB changes, weather updates, travel issues)."];
    if (replay) {
      lines.push(`- STRICT CUTOFF: ${asOf}. This is a blind replay. Use ONLY sources published before the cutoff ` +
        "and ignore anything you know about these games' results or later news. If a source isn't dated " +
        "before the cutoff, don't use it.");
    } else {
      lines.push("- Prefer the newest reports and say when you couldn't find anything.");
    }
    lines.push("Note the most important findings in reasoning / key_factors.");
    return lines.join("\n");
  }

  function cardPrompt(sections, { asOf, replay, title, lessons, prompts }) {
    const teams = sections.flatMap((s) => s.teams);
    const kinds = new Set(sections.map((s) => s.kind));
    const out = [`# ${title}`, "", "## How to pick", prompts.system];
    if (kinds.has("prop")) out.push("", "For the prop questions:", prompts.prop_system);
    out.push("", researchBlock(teams, asOf, replay));
    if (lessons && lessons.length) out.push("", "## Lessons from grading your own past picks (apply them)", ...lessons.map((x) => `  - ${x}`));
    out.push("", "## Items");
    for (const s of sections) {
      const label = { game: "Game", mid_major: "Mid-major game (spread only, no total)", prop: "Question" }[s.kind];
      out.push("", `### Item ${s.idx} - ${label}`, s.text);
    }
    out.push("", "## Answer format",
      "Reply with ONE JSON array in a ```json code block, one object per item, nothing else inside the block.",
      "Game items:", prompts.game_spec);
    if (kinds.has("prop")) out.push("Question items:", prompts.prop_spec);
    out.push('For a game with no total, use "ou_pick": null. Pick against the line shown for each item.');
    return out.join("\n");
  }

  function reviewPrompt(rows, weekStats, season, week, priorLessons, prompts) {
    const payload = { season, week, stats: weekStats, picks: rows, lessons_you_were_using: priorLessons || [] };
    const schema = JSON.parse(JSON.stringify(prompts.review_schema));
    return [`# Self-review of my week ${week} pool picks (${season})`, "", prompts.review_system, "",
      "Here is the data (JSON):", "```json", JSON.stringify(payload, null, 1), "```", "",
      "## Answer format",
      "Reply with ONE JSON object in a ```json code block that matches this JSON schema:",
      "```json", JSON.stringify(schema, null, 1), "```"].join("\n");
  }

  function finalizeReview(out) {
    if (!out || typeof out !== "object" || Array.isArray(out)) throw new Error("The review answer should be one JSON object.");
    const missing = ["items", "summary", "lessons"].filter((k) => !(k in out));
    if (missing.length) throw new Error(`Review answer is missing: ${missing.join(", ")}`);
    const lessons = [];
    for (const l of out.lessons || []) {
      if (l && typeof l === "object" && String(l.text || "").trim()) {
        const at = ["ats", "ou", "props", "all"].includes(l.applies_to) ? l.applies_to : "all";
        lessons.push({ text: String(l.text).trim(), applies_to: at, evidence: String(l.evidence || "") });
      }
    }
    out.lessons = lessons.slice(0, 5);
    if (!Array.isArray(out.items)) out.items = [];
    out.items = out.items.filter((x) => x && typeof x === "object").map((x) => ({
      idx: Number(x.idx), type: String(x.type || ""), verdict: String(x.verdict || "wrong_read"),
      miss_category: String(x.miss_category || "other"), what_happened: String(x.what_happened || ""), lesson: String(x.lesson || "") }));
    if (!("calibration_note" in out)) out.calibration_note = "";
    out.summary = String(out.summary || "");
    return out;
  }

  const api = {
    pyRound, fmtG, fmtSigned, gradeAts, gradeOu, formatSpread, norm, canonical, ratio, getCloseMatch,
    parseCard, matchTeam, matchGames, interpretProp, choiceOf, exportText, gradeGameItem, propActual, gradeProp, whichTeam, parseEntry,
    LINE_RE, DEFAULT_SCORING, merged, actualCombined, scoreEntry, tiebreakPoints, weekComplete, scoreWeek, leaderboard,
    candidateConfigs, describeRule, inferRules, DEFAULT_HIGHLIGHTS, normName, namesMatch, highlightFor, parseLeagueSheet, pickLabel, weekSheet, seasonSheet, toCsv,
    weekItems, stats, calibrationLines, headerLines, rowFromData, propFromData, propPrompt,
    extractJson, answersByItem, researchBlock, cardPrompt, reviewPrompt, finalizeReview,
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.Logic = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
