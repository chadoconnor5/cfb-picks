// CFB Picks: three tabs (This Week, Standings, More). All data work happens in store.js / logic.js.
const $ = (sel, root = document) => root.querySelector(sel);
const api = (path, opts) => Store.api(path, opts);
const S = { season: null, week: null, type: "regular", tab: "week", standingsView: "week", editLines: false, fixPicks: false };

// ---------- helpers ----------

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

let toastTimer = null;
function toast(msg, bad = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("bad", bad);
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), bad ? 6000 : 3000);
}

async function busy(btn, label, fn) {
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = label;
  try { await fn(); } catch (e) { toast(e.message, true); } finally { btn.disabled = false; btn.textContent = orig; }
}

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; }
  catch {
    const ta = el("textarea", {}, text);
    document.body.append(ta); ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  }
}

function download(text, filename, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = el("a", { href: url, download: filename });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const when = (iso) => new Date(iso).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
const signed = (n) => (n > 0 ? `+${n}` : `${n}`);
const weekQuery = () => new URLSearchParams({ season: S.season, week: S.week, season_type: S.type });

function lineText(d) {   // "Ohio State -3.5" (the favorite), from the home team's spread
  if (d.home_spread === 0) return "Pick'em";
  return d.home_spread < 0 ? `${d.home} ${d.home_spread}` : `${d.away} -${d.home_spread}`;
}

function dialog(title, ...body) {
  const close = () => o.remove();
  const o = el("div", { class: "overlay", onclick: (e) => { if (e.target === o) close(); } },
    el("div", { class: "dialog" }, el("div", { class: "dialog-head" }, el("h3", {}, title), el("button", { class: "chip-btn", onclick: close }, "✕")), ...body));
  document.body.append(o);
  return close;
}

// ---------- week + tabs ----------

function showWeek() {
  $("#weekLabel").textContent = S.week ? `Week ${S.week}` : "Week –";
  try { localStorage.setItem("cfbPicks.week", JSON.stringify({ season: S.season, week: S.week })); } catch {}
}

async function render() {
  showWeek();
  const panel = $(`#${S.tab}`);
  try {
    if (S.tab === "week") await renderWeek(panel);
    else if (S.tab === "standings") await renderStandings(panel);
    else await renderMore(panel);
  } catch (e) { panel.replaceChildren(el("p", { class: "banner" }, e.message)); }
}

$("#prevWeek").addEventListener("click", () => { if (S.week > 1) { S.week--; render(); } });
$("#nextWeek").addEventListener("click", () => { if (S.week < 20) { S.week++; render(); } });
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => {
  S.tab = t.dataset.tab;
  document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === t));
  for (const id of ["week", "standings", "more"]) $(`#${id}`).classList.toggle("hidden", id !== S.tab);
  render();
}));

// ---------- This Week ----------

async function renderWeek(root) {
  const data = await api(`/api/pool?${weekQuery()}`);
  if (!data.card || !data.items.length) return renderImport(root, "Paste this week's pool email (games, questions, mid-major) and tap Import.");
  const card = data.card, items = data.items;
  const owner = data.owner || "Ron";
  const first = owner.split(" ")[0];
  const pick = async (it, body) => {
    try { await api(`/api/pool/items/${it.id}/pick`, { method: "POST", body: S.fixPicks ? { ...body, fix: true } : body }); await renderWeek(root); }
    catch (e) { toast(e.message, true); }
  };

  const dl = card.deadline ? new Date(card.deadline) : null;
  let due = "";
  if (dl) {
    const ms = dl - new Date();
    due = card.locked ? `Picks locked ${when(card.deadline)}` :
      `Picks due ${when(card.deadline)} (${Math.floor(ms / 86400000)}d ${Math.floor(ms / 3600000) % 24}h left)`;
  }
  const sc = data.score;
  const banner = el("div", { class: `banner ${card.locked ? "locked" : ""}` },
    el("h2", {}, `Week ${card.week}`), el("div", { class: "muted small" }, due),
    sc.graded_possible ? el("div", { class: "scoreline" }, el("span", {}, `${first}: ${sc.points} pts`), el("span", { class: "muted" }, `Claude: ${sc.claude_points} pts`)) : null,
    items.some((it) => it.claude) || card.locked ? null : el("div", { class: "muted small" }, "Claude's picks will show here once Claude's research has been run."));

  const missing = items.filter((it) => (it.kind === "prop" ? !it.my_prop : !it.my_ats || (it.kind === "game" && it.data.total != null && !it.my_ou))).length;
  const fixPicks = items.some((it) => it.locked) ?
    el("div", { class: "actions" }, el("button", { class: S.fixPicks ? "primary" : "" , onclick: () => { S.fixPicks = !S.fixPicks; renderWeek(root); } },
      S.fixPicks ? "Done filling in picks" : `Fill in or fix ${first}'s picks`),
      missing ? el("span", { class: "warnlist small" }, ` ${missing} pick${missing > 1 ? "s" : ""} missing (marked “no pick” below)`) : null) : null;
  const rows = items.map((it, i) => (it.kind === "prop" ? propItem(it, pick) : gameItem(it, pick, first)));

  const tbIn = el("input", { type: "number", inputmode: "numeric", value: card.tiebreaker ?? "", placeholder: data.projected_combined ?? "", disabled: card.locked });
  tbIn.addEventListener("change", async () => {
    try { await api(`/api/pool/cards/${card.id}/tiebreaker`, { method: "POST", body: { value: tbIn.value } }); toast("Saved."); await renderWeek(root); }
    catch (e) { toast(e.message, true); }
  });
  const tiebreak = card.has_tiebreaker ? el("div", { class: "item tb" }, el("strong", {}, "Combined score"), tbIn,
    data.projected_combined ? el("span", { class: "muted small" }, `Claude's projection: ${data.projected_combined}`) : null) : null;

  const copyBtn = el("button", { class: "primary" }, "Copy reply for the email");
  copyBtn.addEventListener("click", async () => toast((await copyText(data.export)) ? "Copied. Paste it into your reply to the pool." : "Couldn't copy; select the text below.", false));

  const fix = card.locked ? null : el("button", { class: "link" }, S.editLines ? "Done fixing lines" : "Fix a spread or total");
  if (fix) fix.addEventListener("click", () => { S.editLines = !S.editLines; renderWeek(root); });

  root.replaceChildren(banner, fixPicks || "", ...rows, tiebreak || "",
    el("div", { class: "actions" }, copyBtn),
    el("details", {}, el("summary", {}, "Show the reply text"), el("textarea", { readonly: true }, data.export)),
    el("div", { class: "actions" }, fix || "",
      el("details", {}, el("summary", {}, card.locked ? "Paste the pool email (fills in missing questions and picks)" : "Paste a different email (replaces this week's card)"), importBox(root))));
}

function seg(options, value, onPick, disabled, result) {
  return el("span", { class: "seg" }, options.map(([v, label]) =>
    el("button", { class: v === value ? `on ${result || ""}` : "", disabled, onclick: () => onPick(v === value ? null : v) }, label)));
}

const mark = (r) => (r ? el("span", { class: `mark ${r}` }, r === "W" ? "✓" : r === "L" ? "✗" : "push") : null);

function gameItem(it, pick, first) {
  const d = it.data, g = it.game, c = it.claude;
  const final = g && g.completed ? el("span", { class: "final" }, ` · Final ${d.away} ${d.flipped ? g.home_points : g.away_points}, ${d.home} ${d.flipped ? g.away_points : g.home_points}`) : "";
  const kick = g && g.start_date && !g.completed ? ` · ${when(g.start_date)}` : "";
  const hasTotal = d.total !== null && d.total !== undefined;
  const ron = el("div", { class: "row" }, el("span", { class: "who" }, first),
    seg([["away", d.away], ["home", d.home]], it.my_ats, (v) => pick(it, { ats: v, ou: it.my_ou }), it.locked && !S.fixPicks, it.ats_result),
    hasTotal ? seg([["over", "Over"], ["under", "Under"]], it.my_ou, (v) => pick(it, { ats: it.my_ats, ou: v }), it.locked && !S.fixPicks, it.ou_result) : null,
    it.locked && (!it.my_ats || (hasTotal && it.kind === "game" && !it.my_ou)) ? el("span", { class: "warnlist small" }, "no pick") : null);
  let claude = null;
  if (c) {
    const team = c.ats_pick === "home" ? d.home : d.away;
    claude = el("div", { class: "row claude-row" }, el("span", { class: "who" }, "Claude"),
      el("span", {}, `${team}${c.ou_pick ? `, ${c.ou_pick === "over" ? "Over" : "Under"}` : ""} `, mark(it.claude_ats_result), c.ou_pick ? mark(it.claude_ou_result) : null,
        el("span", { class: "muted small" }, ` · confidence ${c.confidence}`)),
      el("details", { class: "why" }, el("summary", {}, "why"), el("div", { class: "small" }, c.reasoning,
        c.key_factors && c.key_factors.length ? el("ul", {}, c.key_factors.map((f) => el("li", {}, f))) : null,
        c.conditions_impact ? el("div", { class: "muted" }, c.conditions_impact) : null)));
  }
  return el("div", { class: "item" },
    el("div", { class: "item-head" }, el("span", { class: "item-title" }, `${d.away} @ ${d.home}`),
      it.kind === "mid_major" ? el("span", { class: "badge" }, "Mid-major") : d.weight > 1 ? el("span", { class: "badge" }, `${d.weight} pts`) : null),
    el("div", { class: "item-line" }, lineText(d), hasTotal ? ` · O/U ${d.total}` : "", kick, final),
    d.game_id == null ? el("div", { class: "warnlist small" }, "This game wasn't found in the week's schedule (results won't fill in).") : null,
    ron, claude, S.editLines && !it.locked ? lineEditor(it) : null);
}

function propItem(it, pick) {
  const d = it.data, c = it.claude;
  return el("div", { class: "item" },
    el("div", { class: "item-head" }, el("span", { class: "item-title" }, `Question ${d.n}`),
      it.prop_actual != null ? el("span", { class: "final small" }, `Result: ${it.prop_actual}`) : null),
    el("div", { class: "q-text" }, d.question),
    d.line == null ? el("div", { class: "warnlist small" }, "Over/under number missing: tap “Fix a spread or total” below.") : el("div", { class: "item-line" }, `Over/under ${d.line}`),
    el("div", { class: "row" }, el("span", { class: "who" }, "Pick"),
      seg([["over", "Over"], ["under", "Under"]], it.my_prop, (v) => pick(it, { pick: v }), it.locked && !S.fixPicks, it.prop_result),
      it.locked && !it.my_prop ? el("span", { class: "warnlist small" }, "no pick") : null),
    c ? el("div", { class: "row claude-row" }, el("span", { class: "who" }, "Claude"),
      el("span", {}, `${c.pick === "over" ? "Over" : "Under"} `, mark(it.claude_prop_result), el("span", { class: "muted small" }, ` · projects ${c.projection} · confidence ${c.confidence}`)),
      el("details", { class: "why" }, el("summary", {}, "why"), el("div", { class: "small" }, c.reasoning))) : null,
    it.kicked_off && it.prop_actual == null ? actualEditor(it) : null,
    S.editLines && !it.locked ? lineEditor(it) : null);
}

function lineEditor(it) {
  const d = it.data;
  if (it.kind === "prop") {
    const inp = el("input", { type: "number", step: "0.5", value: d.line ?? "" });
    return el("div", { class: "edit-line" }, "Over/under", inp, el("button", { onclick: () => saveLine(it, { line: inp.value }) }, "Save"));
  }
  const sp = el("input", { type: "number", step: "0.5", value: d.home_spread });
  const tot = it.kind === "game" ? el("input", { type: "number", step: "0.5", value: d.total ?? "" }) : null;
  const w = it.kind === "game" ? el("input", { type: "number", min: "1", value: d.weight || 1 }) : null;
  return el("div", { class: "edit-line" }, `${d.home} spread`, sp, tot ? ["O/U", tot, "points", w] : null,
    el("button", { onclick: () => saveLine(it, { home_spread: sp.value, total: tot ? tot.value : null, weight: w ? w.value : null }) }, "Save"));
}

async function saveLine(it, body) {
  try { await api(`/api/pool/items/${it.id}/line`, { method: "POST", body }); toast("Saved."); render(); }
  catch (e) { toast(e.message, true); }
}

function actualEditor(it) {
  const inp = el("input", { type: "number", step: "0.5", placeholder: "real number", style: "width:120px" });
  return el("div", { class: "edit-line" }, el("span", { class: "muted" }, "Result not found automatically:"), inp,
    el("button", { onclick: async () => {
      try { await api(`/api/pool/items/${it.id}/actual`, { method: "POST", body: { value: inp.value } }); render(); } catch (e) { toast(e.message, true); }
    } }, "Save"));
}

function importBox(root) {
  const box = el("textarea", { placeholder: "Paste the pool email here…" });
  const btn = el("button", { class: "primary" }, "Import");
  btn.addEventListener("click", () => busy(btn, "Importing…", async () => {
    const r = await api("/api/pool/import", { method: "POST", body: { season: S.season, week: S.week, season_type: S.type, text: box.value } });
    const head = r.filled_locked !== undefined ? `Filled in ${r.filled_locked} missing question line${r.filled_locked === 1 ? "" : "s"} and picks.` : `Imported ${r.games} games and ${r.props} questions.`;
    toast(head + (r.unmatched.length ? ` Check: ${r.unmatched.join(", ")}` : ""), r.unmatched.length > 0);
    await render();
  }));
  return el("div", { class: "empty-card" }, box, btn);
}

function renderImport(root, msg) {
  root.replaceChildren(el("div", { class: "banner" }, el("h2", {}, `Week ${S.week}`), el("div", { class: "muted" }, msg)), importBox(root));
}

// ---------- Standings ----------

async function renderStandings(root) {
  const views = [["week", `Week ${S.week}`], ["season", `Season ${S.season}`]];
  const sub = el("div", { class: "subtabs" }, views.map(([v, label]) =>
    el("button", { class: S.standingsView === v ? "on" : "", onclick: () => { S.standingsView = v; renderStandings(root); } }, label)));
  let body, csvPath;
  if (S.standingsView === "week") {
    let sh = null;
    try { sh = await api(`/api/league/sheet/week?${weekQuery()}`); } catch (e) { body = el("p", { class: "muted" }, e.message); }
    if (sh && !sh.rows.length) body = el("p", { class: "muted" }, "No league picks for this week yet. Add them below.");
    if (sh && sh.rows.length) {
      csvPath = `/api/league/sheet/week?${new URLSearchParams({ ...Object.fromEntries(weekQuery()), format: "csv" })}`;
      const cls = (r) => (r === "W" ? "res-w" : r === "L" ? "res-l" : r === "P" ? "res-p" : "");
      body = el("div", {},
        sh.actual_combined != null ? el("p", { class: "muted small" }, `Combined score of the card games: ${sh.actual_combined}`) : null,
        el("div", { class: "table-wrap" }, el("table", {},
          el("thead", {}, el("tr", {}, el("th", {}, "#"), el("th", { class: "sticky" }, "Player"), el("th", { class: "num" }, "Total"),
            sh.columns.map((c) => el("th", {}, c.label, el("span", { class: "col-sub" }, c.line + (c.weight > 1 ? ` · ${c.weight} pts` : "")))),
            ["Combined guess", "Player of the week"].map((h) => el("th", { class: "num" }, h)))),
          el("tbody", {}, sh.rows.map((r, i) => el("tr", { class: r.highlight ? `hl-${r.highlight}` : "" },
            el("td", {}, i + 1), el("td", { class: "sticky" }, el("strong", {}, r.player)), el("td", { class: "num" }, el("strong", {}, r.total)),
            r.cells.map((c) => el("td", {}, c.label ? [el("span", { class: cls(c.ats_result) }, c.label.split(", ")[0]),
              c.label.includes(", ") ? [", ", el("span", { class: cls(c.ou_result) }, c.label.split(", ")[1])] : null] : "—")),
            el("td", { class: "num" }, r.tiebreaker ?? "—"), el("td", { class: "num" }, r.potw ? el("span", { class: "potw" }, "★ +" + r.potw_points) : "")))))));
    }
  } else {
    const sh = await api(`/api/league/sheet/season?season=${S.season}`);
    csvPath = sh.rows.length ? `/api/league/sheet/season?season=${S.season}&format=csv` : null;
    body = !sh.rows.length ? el("p", { class: "muted" }, "No league picks entered yet.") :
      el("div", { class: "table-wrap" }, el("table", {},
        el("thead", {}, el("tr", {}, el("th", { class: "num" }, "#"), el("th", { class: "sticky" }, "Player"),
          el("th", { class: "num" }, "Total"), el("th", { class: "num" }, "Behind"), sh.weeks.map((w) => el("th", { class: "num" }, `Wk ${w}`)), el("th", { class: "num" }, "★"))),
        el("tbody", {}, sh.rows.map((r) => el("tr", { class: r.highlight ? `hl-${r.highlight}` : "" },
          el("td", { class: "num" }, r.rank), el("td", { class: "sticky" }, el("strong", {}, r.player)),
          el("td", { class: "num" }, el("strong", {}, r.total)), el("td", { class: "num" }, r.behind ? `-${r.behind}` : "—"),
          r.weeks_cells.map((v) => el("td", { class: "num" }, v ?? "—")),
          el("td", { class: "num" }, r.potw ? `★ ${r.potw}` : ""))))));
  }
  const sheetIn = el("textarea", { placeholder: "Paste the whole league sheet for this week (game headers through the last player)…" });
  const sheetBtn = el("button", { class: "primary" }, "Import");
  sheetBtn.addEventListener("click", () => busy(sheetBtn, "Importing…", async () => {
    const r = await api("/api/league/sheet-import", { method: "POST", body: { season: S.season, week: S.week, season_type: S.type, text: sheetIn.value } });
    toast(`Imported ${r.players} players for week ${S.week}.` + (r.warnings.length ? ` ${r.warnings.length} thing(s) to check (see below).` : ""), r.warnings.length > 0);
    S.standingsView = "week";
    await renderStandings(root);
    if (r.warnings.length) root.append(el("div", { class: "section warnlist" }, el("strong", {}, "Check these:"), el("ul", {}, r.warnings.slice(0, 30).map((w) => el("li", {}, w)))));
  }));
  const empty = body && body.textContent && body.textContent.startsWith("No league picks");
  root.replaceChildren(sub, body,
    csvPath ? el("div", { class: "actions" }, el("button", { class: "link", onclick: async () => {
      try { const r = await api(csvPath); download(r.csv, r.filename, "text/csv"); } catch (e) { toast(e.message, true); }
    } }, "Download as a spreadsheet (CSV)")) : null,
    el("details", { class: "section", open: empty && S.standingsView === "week" }, el("summary", {}, `Add the league's picks for week ${S.week}`),
      el("p", { class: "muted small" }, "In the league spreadsheet select everything from the game headers to the last player, copy, and paste here. If this week has no card yet, the sheet creates it."),
      sheetIn, el("div", { class: "actions" }, sheetBtn)));
}

// ---------- More ----------

async function renderMore(root) {
  const sec = (title, ...body) => el("details", { class: "section" }, el("summary", {}, title), ...body);

  // Game research
  const games = await api(`/api/games?${weekQuery()}`);
  const research = sec(`Game research · week ${S.week}`,
    games.length ? games.filter((g) => g.spread !== null || g.total !== null).map((g) => {
      const d = el("details", {}, el("summary", {}, `${g.away_team} @ ${g.home_team}`, el("span", { class: "muted small" }, g.completed ? ` · Final ${g.away_points}-${g.home_points}` : g.start_date ? ` · ${when(g.start_date)}` : "")));
      d.addEventListener("toggle", async () => {
        if (!d.open || d.dataset.loaded) return;
        d.dataset.loaded = "1";
        const c = await api(`/api/games/${g.id}/context`);
        d.append(c.text ? el("pre", { class: "ctx" }, c.text) : el("p", { class: "muted" }, "No pre-game data saved for this game."));
      });
      return d;
    }) : el("p", { class: "muted" }, "No game data for this week yet."));

  // Claude (copy-and-paste, for whoever runs Claude in a chat)
  const pool = await api(`/api/pool?${weekQuery()}`);
  const claude = sec("Claude: copy the card into a Claude chat",
    el("p", { class: "muted small" }, "Normally Chad runs Claude's research. To do it yourself in any Claude chat with web search on: copy the prompt, send it, then paste Claude's reply back here."),
    pool.card ? el("div", { class: "actions" },
      el("button", { onclick: () => claudeDialog("Claude: week card", `/api/pool/cards/${pool.card.id}/prompt`, `/api/pool/cards/${pool.card.id}/answer`) }, "Card prompt"),
      el("button", { onclick: () => claudeDialog("Claude: weekly review", `/api/pool/cards/${pool.card.id}/review/prompt`, `/api/pool/cards/${pool.card.id}/review/answer`) }, "Review prompt (after the games)"))
      : el("p", { class: "muted" }, "Import this week's card first."));

  // Review + lessons
  const rv = await api(`/api/review?season=${S.season}`);
  const rec = (r) => (r && r.w + r.l + r.p ? `${r.w}-${r.l}${r.p ? `-${r.p}` : ""}${r.pct != null ? ` (${r.pct}%)` : ""}` : "—");
  const review = sec("How Claude is doing",
    el("p", {}, `Claude this season: ${rec(rv.season_stats.overall)} · ${(S.owner || "Ron").split(" ")[0]}: ${rec(rv.season_stats.me)}`),
    rv.lessons.length ? [el("h4", {}, "Lessons Claude uses next week"), rv.lessons.map((l) => {
      const cb = el("input", { type: "checkbox", checked: !!l.active });
      cb.addEventListener("change", async () => { await api(`/api/lessons/${l.id}`, { method: "POST", body: { active: cb.checked } }); render(); });
      return el("div", { class: `lesson ${l.active ? "" : "off"}` }, cb, el("div", {}, el("strong", {}, `Week ${l.week}: `), l.text));
    })] : el("p", { class: "muted small" }, "Lessons appear after Claude reviews a finished week."));

  // League scoring
  const cfg = await api("/api/league/scoring");
  const hl = await api("/api/league/highlights");
  const owner = (await api("/api/league/owner")).owner;
  const num = (k, label) => el("label", {}, label, el("input", { type: "number", value: cfg[k], "data-k": k }));
  const fields = [num("ats", "Spread pick"), num("ou", "Over/under"), num("prop", "Question"), num("mid_major", "Mid-major"), num("potw_bonus", "Player of the week")];
  const tbType = el("select", {}, [["none", "no points"], ["closest", "closest guess"], ["exact", "exact guess"], ["within", "within N"], ["tiers", "by rank"]]
    .map(([v, l]) => el("option", { value: v, selected: cfg.tiebreak.type === v }, l)));
  const tbPts = el("input", { type: "text", value: Array.isArray(cfg.tiebreak.points) ? cfg.tiebreak.points.join(",") : (cfg.tiebreak.points ?? "") });
  const bonus = el("select", {}, [["ats", "spread pick worth N"], ["both", "spread and O/U worth N"], ["ats_plus", "spread worth 3 + N"]]
    .map(([v, l]) => el("option", { value: v, selected: cfg.bonus_game_mode === v }, l)));
  const ownerIn = el("input", { type: "text", value: owner });
  const hlText = el("textarea", { style: "min-height:90px" }, Object.entries(hl).map(([n, c]) => `${n}: ${c}`).join("\n"));
  const saveCfg = el("button", { class: "primary" }, "Save");
  saveCfg.addEventListener("click", () => busy(saveCfg, "Saving…", async () => {
    const b = { bonus_game_mode: bonus.value, potw_ties: cfg.potw_ties, tiebreak: { type: tbType.value } };
    for (const f of fields) { const i = f.querySelector("input"); b[i.dataset.k] = Number(i.value); }
    if (tbType.value === "tiers") b.tiebreak.points = tbPts.value.split(",").map(Number).filter((x) => !isNaN(x));
    else if (tbType.value !== "none") b.tiebreak.points = Number(tbPts.value || 1);
    if (tbType.value === "within") b.tiebreak.margin = cfg.tiebreak.margin || 5;
    await api("/api/league/scoring", { method: "POST", body: b });
    await api("/api/league/owner", { method: "POST", body: { owner: ownerIn.value } });
    const h = {};
    for (const line of hlText.value.split("\n")) {
      const m = /^(.+?)\s*:\s*(red|gold|green|blue)\s*$/i.exec(line.trim());
      if (m) h[m[1].trim()] = m[2].toLowerCase();
    }
    await api("/api/league/highlights", { method: "POST", body: h });
    toast("Saved.");
  }));
  const inferBtn = el("button", {}, "Work out the unknown rules from posted points");
  const inferOut = el("div", {});
  inferBtn.addEventListener("click", () => busy(inferBtn, "Working…", async () => {
    const r = await api(`/api/league/infer?season=${S.season}`);
    inferOut.replaceChildren(el("p", {}, r.message || ""), r.candidates.slice(0, 3).map((c) =>
      el("div", { class: "row" }, el("span", { class: "small" }, `${c.rule} (${c.exact}/${c.of} match)`),
        el("button", { onclick: async () => { await api("/api/league/scoring", { method: "POST", body: c.config }); toast("Applied."); render(); } }, "Use"))));
  }));
  const league = sec("League scoring",
    el("div", { class: "form-grid" }, ...fields, el("label", {}, "5-point game", bonus), el("label", {}, "Combined score", tbType),
      el("label", {}, "Combined points", tbPts), el("label", {}, "Pool tab picks belong to", ownerIn)),
    el("h4", {}, "Row colors (name: red / gold / green / blue)"), hlText,
    el("div", { class: "actions" }, saveCfg),
    el("h4", {}, "Work out the combined-score and 5-point-game rules"),
    el("p", { class: "muted small" }, `Type the points the league posted for a few players (week ${S.week} below; do it for a few weeks), then tap the button.`),
    await postedPoints(), inferBtn, inferOut);

  // Saving + devices + data
  const sync = Store.Sync;
  const idx = await Store.index();
  const tokenIn = el("input", { type: "password", placeholder: "Paste the access key", style: "flex:1;min-width:220px" });
  const connectBtn = el("button", { class: "primary" }, "Connect");
  connectBtn.addEventListener("click", () => busy(connectBtn, "Checking…", async () => {
    await sync.test(tokenIn.value.trim());
    await sync.pull();
    toast("Connected: picks are saved online now.");
    render();
  }));
  const restoreIn = el("input", { type: "file", accept: ".json,application/json", class: "hidden" });
  restoreIn.addEventListener("change", async () => {
    const f = restoreIn.files[0];
    if (!f || !confirm("Replace everything with this backup?")) return;
    try { Store.restore(await f.text()); toast("Backup restored."); render(); } catch (e) { toast(e.message, true); }
  });
  const saving = sec("Saving, devices & data",
    sync.connected()
      ? [el("p", {}, sync.status === "error" ? `⚠ ${sync.message}` : "✓ This device saves online."),
         el("p", { class: "muted small" }, "To connect another phone or computer, send this link privately (anyone with it can change the picks):"),
         el("div", { class: "row" }, el("button", { onclick: async () => toast((await copyText(sync.shareLink())) ? "Link copied." : "Couldn't copy.") }, "Copy connect link"),
           el("button", { class: "link", onclick: () => { if (confirm("Stop saving online on this device?")) { sync.disconnect(); render(); } } }, "Disconnect"))]
      : [el("p", {}, "This device saves only in this browser. Paste the access key (or open the connect link) to save online:"), el("div", { class: "row" }, tokenIn, connectBtn)],
    el("h4", {}, "Game data"),
    el("p", { class: "muted small" }, idx.generated_at ? `Updated ${when(idx.generated_at)}. It refreshes by itself a few times a week.` : "No game data yet."),
    el("div", { class: "row" },
      el("button", { onclick: async () => { const r = await api("/api/sync", { method: "POST", body: { season: S.season, week: S.week, season_type: S.type } }); toast(`Reloaded: ${r.games} games this week.`); render(); } }, "Reload game data"),
      el("button", { onclick: () => download(Store.backup(), `cfb-picks-backup-${new Date().toISOString().slice(0, 10)}.json`, "application/json") }, "Download backup"),
      el("button", { onclick: () => restoreIn.click() }, "Restore backup"), restoreIn));

  root.replaceChildren(research, claude, review, league, saving);
}

async function postedPoints() {
  let w;
  try { w = await api(`/api/league/week?${weekQuery()}`); } catch { return el("p", { class: "muted small" }, "Import this week's league picks first."); }
  if (!w.rows.length) return el("p", { class: "muted small" }, "Import this week's league picks first.");
  return el("div", { class: "table-wrap" }, el("table", {}, el("thead", {}, el("tr", {}, el("th", {}, "Player"), el("th", { class: "num" }, "Ours"), el("th", {}, "League posted"))),
    el("tbody", {}, w.rows.map((r) => {
      const inp = el("input", { type: "number", value: r.reported_points ?? "", style: "width:90px" });
      inp.addEventListener("change", async () => {
        await api(r.from_pool ? `/api/league/owner-points/${w.card_id}` : `/api/league/entries/${r.entry_id}`, { method: "POST", body: { reported_points: inp.value } });
        toast("Saved.");
      });
      return el("tr", {}, el("td", {}, r.player), el("td", { class: "num" }, r.total), el("td", {}, inp));
    }))));
}

function claudeDialog(title, promptPath, answerPath) {
  const promptBox = el("textarea", { readonly: true }, "Building the prompt…");
  const answerBox = el("textarea", { placeholder: "Paste Claude's whole reply here." });
  const copy = el("button", {}, "Copy prompt");
  copy.addEventListener("click", async () => { await copyText(promptBox.value); copy.textContent = "Copied ✓"; });
  const save = el("button", { class: "primary" }, "Save Claude's answer");
  const msg = el("div", { class: "warnlist" });
  const close = dialog(title, el("p", { class: "muted small" }, "1. Copy this into a new chat at claude.ai with web search on."), promptBox,
    el("div", { class: "actions" }, copy), el("p", { class: "muted small" }, "2. Paste Claude's reply:"), answerBox, msg, el("div", { class: "actions" }, save));
  api(promptPath).then((r) => { promptBox.value = r.prompt; }).catch((e) => { promptBox.value = ""; msg.textContent = e.message; });
  save.addEventListener("click", () => busy(save, "Saving…", async () => {
    try {
      const r = await api(answerPath, { method: "POST", body: { answer: answerBox.value } });
      if (r.errors && r.errors.length) msg.textContent = "Saved, but check: " + r.errors.join(" ");
      else { close(); toast("Saved Claude's answer."); render(); }
    } catch (e) { msg.textContent = e.message; }
  }));
}

// ---------- sync badge ----------

function renderSync() {
  const s = Store.Sync, b = $("#syncBadge");
  const on = s.connected();
  b.textContent = !on ? "☁ Local" : s.status === "error" ? "☁ Not saved" : s.status === "synced" ? "☁ Saved" : "☁ Saving…";
  b.title = !on ? "Saved only on this device. Tap to connect." : s.message || "Saved online";
  b.classList.toggle("ok", on && s.status === "synced");
  b.classList.toggle("bad", on && s.status === "error");
}
$("#syncBadge").addEventListener("click", () => document.querySelector('.tab[data-tab="more"]').click());
Store.Sync.listeners.add(renderSync);
window.addEventListener("focus", async () => {
  if (Store.Sync.connected() && !document.querySelector(".overlay") && (await Store.Sync.pull())) render();
});

// ---------- start ----------

(async function init() {
  renderSync();
  if (Store.Sync.connected()) await Store.Sync.pull();
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem("cfbPicks.week")); } catch {}
  const cw = await api("/api/current-week").catch(() => ({}));
  S.season = cw.season || (saved && saved.season) || new Date().getFullYear();
  S.week = cw.week || (saved && saved.week) || 1;
  try { S.owner = (await api("/api/league/owner")).owner; } catch {}
  render();
})();
