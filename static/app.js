const $ = (sel, root = document) => root.querySelector(sel);
const state = { games: [] };

// ---------- utils ----------

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

// Append children, skipping null/false (Node.append would print them as text).
function put(node, ...kids) {
  node.append(...kids.flat().filter((k) => k !== null && k !== undefined && k !== false));
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

function status(msg, isError = false) {
  const s = $("#status");
  s.textContent = msg || "";
  s.classList.toggle("error", isError);
}

function fmtSpread(team, spread) {
  if (spread === null || spread === undefined) return `${team} (no line)`;
  if (spread === 0) return `${team} PK`;
  return `${team} ${spread > 0 ? "+" : ""}${spread}`;
}

function favLine(g, spread = g.spread) {
  if (spread === null || spread === undefined) return "No spread";
  if (spread === 0) return "Pick'em";
  return spread < 0 ? fmtSpread(g.home_team, spread) : fmtSpread(g.away_team, -spread);
}

function sideLabel(g, side, spread) {
  if (side === "home") return fmtSpread(g.home_team, spread);
  if (side === "away") return fmtSpread(g.away_team, spread === null ? null : -spread);
  return "";
}

function kickoff(g) {
  if (!g.start_date) return "TBD";
  const d = new Date(g.start_date);
  return d.toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

const res = (r) => (r ? el("span", { class: `res ${r}` }, r) : null);

function weekParams() {
  return {
    season: Number($("#season").value),
    week: Number($("#week").value),
    season_type: $("#seasonType").value,
  };
}

function saveControls() {
  try { localStorage.setItem("cfbPicks.controls", JSON.stringify(weekParams())); } catch {}
}

// ---------- slate ----------

async function loadGames() {
  const p = weekParams();
  if (!p.season || !p.week) return status("Set a season and week.", true);
  saveControls();
  const q = new URLSearchParams(p).toString();
  state.games = await api(`/api/games?${q}`);
  renderSlate();
  if (!state.games.length) status("No games stored for this week yet. Click “Sync slate & lines”.");
}

function renderSlate() {
  const root = $("#slate");
  root.replaceChildren();
  const term = $("#search").value.trim().toLowerCase();
  const onlyLines = $("#onlyLines").checked;
  const games = state.games.filter((g) =>
    (!onlyLines || g.spread !== null || g.total !== null || g.pick) &&
    (!term || g.home_team.toLowerCase().includes(term) || g.away_team.toLowerCase().includes(term))
  );
  if (!games.length) {
    root.append(el("div", { class: "empty" }, state.games.length ? "No games match the filters." : "No games loaded."));
    return;
  }
  for (const g of games) root.append(renderGame(g));
}

function renderGame(g) {
  const node = $("#gameTpl").content.firstElementChild.cloneNode(true);
  node.dataset.id = g.id;
  if (g.pick) node.classList.add("picked");

  const final = g.completed && g.home_points !== null;
  const teamRow = (name, pts) =>
    [el("span", {}, name), final ? el("span", { class: "score" }, pts) : ""];
  $(".team.away", node).append(...teamRow(g.away_team, g.away_points));
  $(".team.home", node).append(...teamRow(`${g.neutral_site ? "vs" : "@"} ${g.home_team}`, g.home_points));
  if (final) {
    if (g.home_points > g.away_points) $(".team.home", node).classList.add("winner");
    if (g.away_points > g.home_points) $(".team.away", node).classList.add("winner");
  }

  put($(".meta", node),
    el("div", {}, final ? "Final" : g.kicked_off ? "In progress" : kickoff(g)),
    g.venue ? el("div", {}, g.venue) : null,
  );

  const lines = $(".lines", node);
  lines.append(el("span", {}, el("strong", {}, favLine(g))));
  lines.append(el("span", {}, `O/U `, el("strong", {}, g.total ?? "—")));
  if (g.spread_open !== null && g.spread_open !== g.spread) {
    lines.append(el("span", { class: "moved" }, `opened ${favLine(g, g.spread_open)}`));
  }
  if (g.provider) lines.append(el("span", { class: "moved" }, g.provider));

  const notes = $(".notes", node);
  notes.value = g.notes || "";
  if (g.notes) $(".notes-wrap", node).open = true;
  $(".save-notes", node).addEventListener("click", async () => {
    try {
      await api(`/api/games/${g.id}/notes`, { method: "POST", body: { notes: notes.value } });
      g.notes = notes.value;
      status("Notes saved.");
    } catch (e) { status(e.message, true); }
  });

  renderConditions(g, $(".conditions", node));
  const moreWrap = $(".more-wrap", node);
  moreWrap.addEventListener("toggle", async () => {
    if (!moreWrap.open || moreWrap.dataset.loaded) return;
    moreWrap.dataset.loaded = "1";
    const box = $(".more", node);
    box.textContent = "Loading…";
    try {
      const c = await api(`/api/games/${g.id}/context`);
      const sec = (title, body) => body ? [el("h4", {}, title), el("pre", {}, body)] : null;
      box.replaceChildren(
        ...(sec("Situation", (c.situation_flags || []).length ? c.situation_flags.map((f) => "• " + f).join("\n") : "No notable spots.") || []),
        ...(sec("Home field & crowd", c.hfa_text) || []),
        ...Object.entries(c.coaching_text || {}).flatMap(([t, txt]) => sec(`Coaching: ${t}`, txt) || []),
        ...Object.entries(c.staff_text || {}).flatMap(([t, txt]) => sec(`Staff track records: ${t}`, txt) || []),
        ...Object.entries(c.intel || {}).flatMap(([t, d]) => sec(`Program intel: ${t}`, [
          d.hot_seat ? `Hot seat: ${d.hot_seat.status}${d.hot_seat.evidence ? " - " + d.hot_seat.evidence : ""}` : null,
          ...(d.coaching_changes || []).map((x) => `Change: ${x.text} (${x.date || "undated"})`),
          ...(d.rumors || []).map((x) => `Rumor: ${x.text} (${x.date || "undated"})`),
          d.nil && d.nil.estimate ? `NIL (estimate): ${d.nil.estimate}` : null,
          ...(d.roster_notes || []).map((x) => `Roster: ${x.text} (${x.date || "undated"})`),
        ].filter(Boolean).join("\n")) || []),
        ...Object.entries(c.fcs_text || {}).flatMap(([t, txt]) => sec(`FCS history: ${t}`, txt) || []),
        Object.keys(c.intel || {}).length ? "" : el("p", { class: "hint" }, "No program intel yet: it's gathered when you ask Claude with “Program intel” checked."),
      );
    } catch (e) { box.textContent = e.message; }
  });
  renderInjuries(g, $(".injuries", node), $(".injuries-wrap", node));
  renderClaude(g, $(".claude", node), notes);
  renderPick(g, $(".pick", node));
  return node;
}

const pctTxt = (v) => (v === null || v === undefined ? "—" : `${v}`);

function weatherLine(g) {
  const a = g.assessment || {};
  const c = a.conditions;
  const w = g.weather;
  if (c && c.dome) return el("div", { class: "wx" }, "🏟 Indoors");
  if (!w) return el("div", { class: "wx tag" }, g.completed ? "" : "No forecast yet");
  const icon = c && c.precip_type === "snow" ? "❄️" : c && c.precip_type === "rain" ? "🌧" : (w.wind_mph || 0) >= 15 ? "💨" : "☀️";
  return el("div", { class: "wx" },
    el("span", {}, `${icon} ${Math.round(w.temp_f)}°F`),
    el("span", {}, `wind ${Math.round(w.wind_mph)} mph (gusts ${Math.round(w.gust_mph)})`),
    el("span", {}, `${Math.round(w.precip_prob ?? 0)}% precip${w.precip_in ? `, ${w.precip_in}"` : ""}${w.snow_in ? `, ${w.snow_in}" snow` : ""}`),
    c ? el("span", { class: `sev ${c.level}` }, c.level === "none" ? "no impact" : `${c.level} impact`) : null);
}

function renderConditions(g, root) {
  const a = g.assessment;
  root.append(weatherLine(g));
  if (!a) return;

  const chips = ["away", "home"].map((side) => {
    const t = a.teams[side];
    if (!t || t.style === "unknown") return null;
    const label = ["exposed", "insulated", "neutral"].includes(t.label) ? ` · ${t.label}` : "";
    return el("span", { class: `chip ${t.label}`, title: t.reasons.join("\n") }, `${t.team}: ${t.style}${label}`);
  });
  if (chips.some(Boolean)) root.append(el("div", { class: "styles" }, chips));

  const pa = g.profiles?.away, ph = g.profiles?.home;
  const details = el("details", {}, el("summary", {}, "Matchup & conditions"));
  if (a.notes.length) details.append(el("ul", { class: "cond-notes" }, a.notes.map((n) => el("li", {}, n))));
  if (a.historical && a.historical.length) {
    details.append(el("p", { class: "hint" }, el("strong", {}, "Historical evidence (backtest)")));
    details.append(el("ul", { class: "cond-notes" }, a.historical.map((h) => el("li", {}, h))));
  }
  for (const side of ["away", "home"]) {
    const t = a.teams[side];
    if (t && t.reasons.length) details.append(el("p", { class: "hint" }, `${t.team}: ${t.reasons.join("; ")}.`));
  }
  if (pa || ph) {
    const row = (label, fn) => el("tr", {}, el("td", {}, label), el("td", {}, fn(pa)), el("td", {}, fn(ph)));
    const pct = (key) => (p) => (p ? pctTxt(p.pct[key]) : "—");
    details.append(el("table", { class: "profile-table" },
      el("thead", {}, el("tr", {}, el("th", {}, "FBS percentile"), el("th", {}, g.away_team), el("th", {}, g.home_team))),
      el("tbody", {},
        row("Pass rate", (p) => (p && p.pass_rate != null ? `${Math.round(p.pass_rate * 100)}%` : "—")),
        row("Pass EPA", pct("pass_ppa")),
        row("Rush EPA", pct("rush_ppa")),
        row("Pass explosiveness", pct("pass_expl")),
        row("Pace", pct("plays_per_game")),
        row("Def vs pass", pct("def_pass_ppa")),
        row("Def vs rush", pct("def_rush_ppa")),
        row("SP+", (p) => (p && p.sp.rating != null ? `${p.sp.rating > 0 ? "+" : ""}${p.sp.rating.toFixed(1)} (#${p.sp.ranking})` : "—")),
      )));
  }
  root.append(details);
  if (g.history) root.append(historyDetails(g));
}

function recTxt(r) {
  if (!r || r.w + r.l + r.p === 0) return "—";
  return `${r.w}-${r.l}${r.p ? `-${r.p}` : ""}`;
}

function historyDetails(g) {
  const h = g.history;
  const row = (label, fn) => el("tr", {}, el("td", {}, label), el("td", {}, fn(h.away)), el("td", {}, fn(h.home)));
  const det = el("details", {}, el("summary", {}, "Betting history"),
    el("table", { class: "profile-table" },
      el("thead", {}, el("tr", {}, el("th", {}, "Closing lines"), el("th", {}, g.away_team), el("th", {}, g.home_team))),
      el("tbody", {},
        row("ATS this season", (t) => recTxt(t.season.all.ats)),
        row("O/U this season (overs first)", (t) => recTxt(t.season.all.ou)),
        row("ATS last 10", (t) => recTxt(t.season.last10.ats)),
        row("ATS 5 yrs", (t) => recTxt(t.five_years.all.ats)),
        row("ATS 5 yrs home / away", (t) => `${recTxt(t.five_years.home.ats)} / ${recTxt(t.five_years.away.ats)}`),
        row("ATS 5 yrs fav / dog", (t) => `${recTxt(t.five_years.favorite.ats)} / ${recTxt(t.five_years.underdog.ats)}`),
        row("Rush yds/g (allowed)", (t) => t.stats.for.rush_yds ? `${t.stats.for.rush_yds.avg} (${t.stats.against.rush_yds?.avg ?? "—"})` : "—"),
        row("Total yds/g (allowed)", (t) => t.stats.for.total_yds ? `${t.stats.for.total_yds.avg} (${t.stats.against.total_yds?.avg ?? "—"})` : "—"),
        row("Sacks/g (allowed)", (t) => t.stats.for.sacks ? `${t.stats.for.sacks.avg} (${t.stats.against.sacks?.avg ?? "—"})` : "—"),
      )));
  if (h.h2h.length) {
    det.append(el("p", { class: "hint" }, `Recent meetings (${g.home_team}'s view): ` + h.h2h.map((r) =>
      `${r.date.slice(0, 4)} ${r.points}-${r.opp_points}, line ${r.team_spread ?? "—"} → ${r.ats || "—"}, total ${r.total ?? "—"} → ${r.ou || "—"}`).join(" · ")));
  }
  return det;
}

function renderInjuries(g, root, wrap) {
  const inj = g.injuries;
  if (inj) {
    wrap.querySelector("summary").textContent = `Injuries (updated ${new Date(inj.fetched_at.replace(" ", "T") + "Z").toLocaleString()})`;
    root.append(el("div", { class: "injury-report" }, inj.report));
    if (inj.sources.length) {
      root.append(el("ul", { class: "sources" }, inj.sources.slice(0, 8).map((s) =>
        el("li", {}, el("a", { href: s.url, target: "_blank", rel: "noopener noreferrer" }, s.title)))));
    }
  } else {
    root.append(el("p", { class: "hint" }, "No injury report yet."));
  }
  if (g.completed) return;
  const btn = el("button", { class: "small" }, inj ? "Refresh injury news" : "Search injury news");
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    btn.textContent = "Searching…";
    status(`Searching injury news for ${g.away_team} @ ${g.home_team}…`);
    try {
      await api(`/api/games/${g.id}/injuries`, { method: "POST", body: {} });
      status("Injury report updated.");
      await reloadGame(g.id, { openInjuries: true });
    } catch (e) {
      status(e.message, true);
      btn.disabled = false;
      btn.textContent = "Try again";
    }
  });
  root.append(btn);
}

async function reloadGame(id, { openInjuries = false } = {}) {
  const fresh = await api(`/api/games?${new URLSearchParams(weekParams())}`);
  state.games = fresh;
  const g = fresh.find((x) => x.id === id);
  if (!g) return renderSlate();
  rerender(g);
  if (openInjuries) document.querySelector(`.game[data-id="${id}"] .injuries-wrap`).open = true;
}

function renderClaude(g, root, notesEl) {
  const p = g.prediction;
  const locked = Boolean(g.pick);
  root.append(el("h3", {}, "Claude"));

  if (p) {
    const atsLine = sideLabel(g, p.ats_pick, p.spread);
    put(root,
      el("div", { class: "picks" },
        el("span", { class: "chip claude-chip" }, "ATS: ", atsLine, " ", res(p.ats_result)),
        el("span", { class: "chip claude-chip" }, `${p.ou_pick === "over" ? "Over" : "Under"} ${p.total ?? ""} `, res(p.ou_result)),
        el("span", { class: "chip" }, "Conf ", el("span", { class: "conf" }, p.confidence)),
      ),
      el("div", {}, `Projected: ${g.away_team} ${p.proj_away}, ${g.home_team} ${p.proj_home}`),
      el("p", {}, p.reasoning),
      p.conditions_impact ? el("p", { class: "impact" }, "Weather & injuries: ", p.conditions_impact) : null,
      p.key_factors && p.key_factors.length ? el("ul", { class: "factors" }, p.key_factors.map((f) => el("li", {}, f))) : null,
      el("div", { class: "lock" },
        p.locked ? "🔒 Locked with your pick" : `Generated ${new Date(p.created_at + "Z").toLocaleString()}`,
        p.spread !== g.spread || p.total !== g.total ? " · line has moved since" : ""),
    );
  } else {
    root.append(el("p", { class: "hint" }, locked ? "No Claude pick was made before you locked yours." : "No prediction yet."));
  }

  if (!locked && !g.kicked_off) {
    const btn = el("button", { class: "small" }, p ? "Re-run with current line & notes" : (isManual() ? "Copy for Claude" : "Get Claude's pick"));
    if (isManual()) {
      btn.addEventListener("click", () => claudeModal({
        title: `Claude: ${g.away_team} @ ${g.home_team}`,
        getPrompt: () => api(`/api/games/${g.id}/prompt`, { method: "POST", body: { notes: notesEl.value } }),
        submit: (answer) => api(`/api/games/${g.id}/answer`, { method: "POST", body: { answer } }),
        done: async () => { status("Claude's pick saved."); await reloadGame(g.id); },
      }));
      root.append(btn);
      return;
    }
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      btn.textContent = "Thinking…";
      const searchInjuries = $("#searchInjuries").checked;
      status(`Asking Claude about ${g.away_team} @ ${g.home_team}${searchInjuries ? " (searching injury news first)" : ""}…`);
      try {
        await api(`/api/games/${g.id}/predict`, {
          method: "POST",
          body: { notes: notesEl.value, search_injuries: searchInjuries, search_intel: $("#searchIntel").checked },
        });
        status("Prediction ready.");
        await reloadGame(g.id);
      } catch (e) {
        status(e.message, true);
        btn.disabled = false;
        btn.textContent = "Try again";
      }
    });
    root.append(btn);
  }
}

function seg(name, options, checked) {
  return el("span", { class: "seg" },
    options.map(([value, label]) =>
      el("label", {}, el("input", { type: "radio", name, value, checked: value === checked }), label)));
}

function renderPick(g, root) {
  root.append(el("h3", {}, "My pick"));
  const pk = g.pick;
  const c = g.prediction && g.prediction.locked ? g.prediction : null;

  if (pk) {
    const vs = (mine, theirs) => (!c || !mine ? null : el("span", { class: "tag" }, mine === theirs ? "agree" : "fade"));
    if (pk.ats_pick) {
      root.append(el("div", { class: "pick-row" },
        el("span", { class: "chip" }, sideLabel(g, pk.ats_pick, pk.spread)), res(pk.ats_result), vs(pk.ats_pick, c && c.ats_pick)));
    }
    if (pk.ou_pick) {
      root.append(el("div", { class: "pick-row" },
        el("span", { class: "chip" }, `${pk.ou_pick === "over" ? "Over" : "Under"} ${pk.total}`), res(pk.ou_result), vs(pk.ou_pick, c && c.ou_pick)));
    }
    if (!g.kicked_off) {
      const del = el("button", { class: "small" }, "Delete pick (unlocks Claude)");
      del.addEventListener("click", async () => {
        if (!confirm("Delete this pick? Claude's pick will unlock and can be re-run.")) return;
        try {
          await api(`/api/games/${g.id}/pick`, { method: "DELETE" });
          g.pick = null;
          if (g.prediction) g.prediction.locked = 0;
          rerender(g);
        } catch (e) { status(e.message, true); }
      });
      root.append(del);
    }
    return;
  }

  if (g.kicked_off) {
    root.append(el("p", { class: "hint" }, "No pick. Game has kicked off."));
    return;
  }

  const name = `g${g.id}`;
  const spreadIn = el("input", { type: "number", step: "0.5", value: g.spread ?? "", title: `${g.home_team} spread` });
  const totalIn = el("input", { type: "number", step: "0.5", value: g.total ?? "", title: "Total" });
  const atsSeg = seg(`${name}-ats`, [["away", g.away_team], ["home", g.home_team], ["", "—"]], "");
  const ouSeg = seg(`${name}-ou`, [["over", "Over"], ["under", "Under"], ["", "—"]], "");

  root.append(
    el("div", { class: "pick-row" }, "ATS ", atsSeg, el("span", { class: "tag" }, `${g.home_team} line`), spreadIn),
    el("div", { class: "pick-row" }, "O/U ", ouSeg, el("span", { class: "tag" }, "total"), totalIn),
  );
  if (g.prediction) {
    const agreeBtn = el("button", { class: "small" }, "Copy Claude");
    agreeBtn.addEventListener("click", () => {
      root.querySelector(`input[name="${name}-ats"][value="${g.prediction.ats_pick}"]`).checked = true;
      root.querySelector(`input[name="${name}-ou"][value="${g.prediction.ou_pick}"]`).checked = true;
    });
    const fadeBtn = el("button", { class: "small" }, "Fade Claude");
    fadeBtn.addEventListener("click", () => {
      const ats = g.prediction.ats_pick === "home" ? "away" : "home";
      const ou = g.prediction.ou_pick === "over" ? "under" : "over";
      root.querySelector(`input[name="${name}-ats"][value="${ats}"]`).checked = true;
      root.querySelector(`input[name="${name}-ou"][value="${ou}"]`).checked = true;
    });
    root.append(el("div", { class: "pick-row" }, agreeBtn, fadeBtn));
  }

  const save = el("button", { class: "primary small" }, "Save pick & lock");
  save.addEventListener("click", async () => {
    const ats = root.querySelector(`input[name="${name}-ats"]:checked`)?.value || null;
    const ou = root.querySelector(`input[name="${name}-ou"]:checked`)?.value || null;
    try {
      await api(`/api/games/${g.id}/pick`, {
        method: "POST",
        body: { ats_pick: ats, ou_pick: ou, spread: spreadIn.value, total: totalIn.value },
      });
      status("Pick saved and locked.");
      await loadGames();
    } catch (e) { status(e.message, true); }
  });
  root.append(save);
}

function rerender(g) {
  const old = document.querySelector(`.game[data-id="${g.id}"]`);
  if (old) old.replaceWith(renderGame(g));
}

// ---------- record ----------

function statCard(title, rec, hint) {
  const line = (label, t) =>
    el("div", { class: "line" },
      el("span", {}, label),
      el("span", {}, `${t.W}-${t.L}${t.P ? `-${t.P}` : ""} `, el("span", { class: "pct" }, t.pct === null ? "" : `(${t.pct}%)`)));
  return el("div", { class: "stat" },
    el("h4", {}, title),
    line("ATS", rec.ats),
    line("O/U", rec.ou),
    hint ? el("p", { class: "hint" }, hint) : null);
}

async function loadRecord() {
  const season = Number($("#season").value) || "";
  const r = await api(`/api/record${season ? `?season=${season}` : ""}`);
  const root = $("#record");
  root.replaceChildren(
    el("h2", {}, `Record${season ? ` · ${season}` : ""}`),
    el("p", { class: "hint" }, "52.4% is break-even at -110."),
    el("div", { class: "stat-grid" },
      statCard("Me", r.me),
      statCard("Claude (locked with my picks)", r.claude_locked, "Graded on the line Claude saw."),
      statCard("Claude (every game it picked)", r.claude_all),
      statCard("Me when I agreed with Claude", r.agree),
      statCard("Me when I faded Claude", r.fade),
    ),
    el("h2", {}, "Claude by confidence"),
    el("div", { class: "stat-grid" }, r.confidence.map((b) => statCard(`Confidence ${b.range}`, b))),
    el("h2", {}, "Pick history"),
    r.history.length
      ? el("div", { class: "table-wrap" }, el("table", {},
          el("thead", {}, el("tr", {}, ["Wk", "Game", "Final", "My ATS", "", "My O/U", "", "Claude ATS", "", "Claude O/U", "", "Conf"].map((h) => el("th", {}, h)))),
          el("tbody", {}, r.history.map((h) => el("tr", {},
            el("td", {}, `${h.season_type === "postseason" ? "P" : ""}${h.week}`),
            el("td", {}, `${h.away_team} @ ${h.home_team}`),
            el("td", {}, h.completed ? `${h.away_points}-${h.home_points}` : "—"),
            el("td", {}, h.ats_pick ? sideLabel(h, h.ats_pick, h.spread) : ""), el("td", {}, res(h.ats_result)),
            el("td", {}, h.ou_pick ? `${h.ou_pick === "over" ? "O" : "U"} ${h.total}` : ""), el("td", {}, res(h.ou_result)),
            el("td", {}, h.c_ats ? sideLabel(h, h.c_ats, h.c_spread) : ""), el("td", {}, res(h.c_ats_result)),
            el("td", {}, h.c_ou ? `${h.c_ou === "over" ? "O" : "U"} ${h.c_total}` : ""), el("td", {}, res(h.c_ou_result)),
            el("td", {}, h.c_conf ?? ""),
          )))))
      : el("p", { class: "empty" }, "No picks yet."),
  );
}

// ---------- pool ----------

const STAT_NAMES = { rush_yds: "rushing yards", pass_yds: "passing yards", total_yds: "total yards", sacks: "sacks", points: "points", turnovers: "turnovers" };

function poolSelect(options, value, onChange, disabled) {
  const sel = el("select", { disabled }, options.map(([v, label]) => el("option", { value: v, selected: v === (value || "") }, label)));
  sel.addEventListener("change", () => onChange(sel.value || null));
  return sel;
}

async function loadPool() {
  const p = weekParams();
  if (!state.games.length && p.week) { try { await loadGames(); } catch {} }
  const root = $("#pool");
  const data = await api(`/api/pool?${new URLSearchParams(p)}`);
  const importBox = el("textarea", { placeholder: "Paste this week's pool email here (games, Questions, Mid-Major Contest, Combined Score)…" });
  const importBtn = el("button", { class: "primary" }, data.card ? "Re-import card (replaces this week)" : "Import card");
  importBtn.addEventListener("click", () => withBusy(importBtn, "Importing…", async () => {
    const r = await api("/api/pool/import", { method: "POST", body: { ...p, text: importBox.value } });
    status(`Imported ${r.games} games and ${r.props} questions.` + (r.unmatched.length ? ` Check: ${r.unmatched.join(", ")}` : ""), r.unmatched.length > 0);
    await loadPool();
  }));
  const importSection = el("details", { class: "pool-import", open: !data.card },
    el("summary", {}, data.card ? "Import a different card" : "Import this week's card"), importBox, el("div", { class: "pool-actions" }, importBtn));

  if (!data.card) {
    const blankBtn = el("button", {}, "Start a blank card (enter games and lines by hand)");
    blankBtn.addEventListener("click", () => withBusy(blankBtn, "Creating…", async () => {
      await api("/api/pool/cards", { method: "POST", body: p });
      await loadPool();
    }));
    root.replaceChildren(el("h2", {}, `Pool · week ${p.week}`), importSection, el("div", { class: "pool-actions" }, blankBtn));
    return;
  }
  const items = data.items;
  const save = async (it, body) => {
    try { await api(`/api/pool/items/${it.id}/pick`, { method: "POST", body }); await loadPool(); }
    catch (e) { status(e.message, true); }
  };

  const rows = items.map((it, i) => {
    const d = it.data, c = it.claude, locked = it.locked, num = i + 1;
    const stale = it.claude_stale ? el("div", { class: "warn" }, "Line changed since Claude predicted: re-run") : null;
    const del = locked ? null : el("button", { class: "small", title: "Remove from card", onclick: async () => {
      if (!confirm("Remove this item from the card?")) return;
      try { await api(`/api/pool/items/${it.id}`, { method: "DELETE" }); await loadPool(); } catch (e) { status(e.message, true); }
    } }, "✕");
    const canReplay = data.card.locked;
    const predictBtn = el("button", { class: "small", disabled: (locked && !canReplay) || (d.game_id == null) }, c ? "Re-run" : (canReplay ? "Replay" : (isManual() ? "Copy for Claude" : "Predict")));
    if (isManual()) {
      predictBtn.addEventListener("click", () => claudeModal({
        title: `Claude: item ${num}`,
        getPrompt: () => api(`/api/pool/cards/${data.card.id}/prompt?items=${it.id}`),
        submit: (answer) => api(`/api/pool/cards/${data.card.id}/answer`, { method: "POST", body: { answer, item_id: it.id } }),
        done: loadPool,
      }));
    } else {
      predictBtn.addEventListener("click", () => withBusy(predictBtn, "…", async () => {
        await api(`/api/pool/items/${it.id}/predict`, { method: "POST", body: {} });
        await loadPool();
      }));
    }
    if (it.kind === "prop") {
      const interp = d.team && d.stat ? `${d.scope === "combined" ? `${d.team} + ${d.opponent} combined` : d.team} ${STAT_NAMES[d.stat] || d.stat}` : "⚠ couldn't interpret";
      const lineIn = el("input", { type: "number", step: "0.5", value: d.line ?? "", disabled: locked, class: "line-in" });
      const saveLine = el("button", { class: "small", disabled: locked }, "Save");
      saveLine.addEventListener("click", async () => {
        try { await api(`/api/pool/items/${it.id}/line`, { method: "POST", body: { line: lineIn.value } }); status("Line saved."); await loadPool(); }
        catch (e) { status(e.message, true); }
      });
      return el("tr", {},
        el("td", {}, el("span", { class: "num" }, `#${num}`), ` Q${d.n}`, del),
        el("td", { class: "q" }, d.question, el("div", { class: "line-edit" }, "O/U ", lineIn, saveLine),
          el("div", { class: "hint" }, interp)),
        el("td", { class: "pool-claude" }, c ? [el("strong", {}, `${c.pick.toUpperCase()} (${c.confidence})`), ` proj ${c.projection}`, el("div", { class: "why" }, c.reasoning), stale] : "—", el("div", {}, predictBtn)),
        el("td", {}, poolSelect([["", "—"], ["over", "Over"], ["under", "Under"]], it.my_prop, (v) => save(it, { pick: v }), locked),
          c && !locked ? el("button", { class: "small", onclick: () => save(it, { pick: c.pick }) }, "Use Claude") : null),
        el("td", {}, it.prop_actual != null ? `${it.prop_actual} ` : "", res(it.prop_result), it.claude_prop_result ? el("div", { class: "hint" }, "Claude ", res(it.claude_prop_result)) : null),
      );
    }
    const g = it.game;
    const spreadIn = el("input", { type: "number", step: "0.5", value: d.home_spread ?? "", disabled: locked, class: "line-in" });
    const totalIn = it.kind === "game" ? el("input", { type: "number", step: "0.5", value: d.total ?? "", disabled: locked, class: "line-in" }) : null;
    const weightIn = it.kind === "game" ? el("input", { type: "number", min: "1", step: "1", value: d.weight || 1, disabled: locked, class: "line-in small-in", title: "Points per correct pick" }) : null;
    const saveLine = el("button", { class: "small", disabled: locked }, "Save line");
    saveLine.addEventListener("click", async () => {
      try {
        await api(`/api/pool/items/${it.id}/line`, { method: "POST", body: { home_spread: spreadIn.value, total: totalIn ? totalIn.value : null, weight: weightIn ? weightIn.value : null } });
        status("Line saved."); await loadPool();
      } catch (e) { status(e.message, true); }
    });
    const mk = it.market;
    const market = mk && (mk.home_spread != null || mk.total != null)
      ? el("div", { class: "hint" }, `Market now: ${d.home} ${mk.home_spread == null ? "—" : (mk.home_spread > 0 ? "+" : "") + mk.home_spread}` +
          (it.kind === "game" ? `, o/u ${mk.total ?? "—"}` : "") +
          ((mk.home_spread != null && mk.home_spread !== d.home_spread) || (it.kind === "game" && mk.total != null && mk.total !== d.total) ? " (moved)" : ""))
      : null;
    const entered = d.line_entered_at ? el("div", { class: "hint" }, `Line entered ${new Date(d.line_entered_at).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" })}`) : null;
    const line = el("div", {}, el("strong", {}, `${d.away} @ ${d.home}`),
      el("div", { class: "line-edit", title: "Home team's spread: + means home is the underdog, - means home is favored" }, `${d.home} spread `, spreadIn, totalIn ? [" O/U ", totalIn] : null, weightIn ? [" pts ", weightIn] : null, " ", saveLine),
      market, entered);
    const final = g && g.completed ? ` · Final ${g.away_points}-${g.home_points}` : "";
    const claudeTeam = c ? (c.ats_pick === "home" ? d.home : d.away) : null;
    return el("tr", {},
      el("td", {}, el("span", { class: "num" }, `#${num} `), it.kind === "mid_major" ? "Mid-major" : d.weight > 1 ? el("span", { class: "bonus" }, `${d.weight}-pt`) : "", del),
      el("td", {}, line, d.game_id == null ? el("div", { class: "warn" }, "Not matched to a game on the slate") : null,
        el("div", { class: "hint" }, g ? (g.completed ? final.slice(3) : kickoff(g)) : "")),
      el("td", { class: "pool-claude" }, c ? [el("strong", {}, `${claudeTeam}${c.ou_pick ? `, ${c.ou_pick === "over" ? "O" : "U"}` : ""} (${c.confidence})`),
        ` ${d.away} ${c.proj_away}-${c.proj_home} ${d.home}`, c.replay ? el("span", { class: "replay", title: `Strict replay as of ${c.as_of}` }, "REPLAY") : null,
        el("div", { class: "why" }, c.reasoning), stale,
        c.audit && c.audit.length ? el("details", {}, el("summary", { class: "hint" }, "Leakage audit"), el("ul", { class: "audit" }, c.audit.map((a) => el("li", {}, a)))) : null] : "—", el("div", {}, predictBtn)),
      el("td", {},
        poolSelect([["", "—"], ["away", d.away], ["home", d.home]], it.my_ats, (v) => save(it, { ats: v, ou: it.my_ou }), locked),
        d.total != null ? poolSelect([["", "—"], ["over", "Over"], ["under", "Under"]], it.my_ou, (v) => save(it, { ats: it.my_ats, ou: v }), locked) : null,
        c && !locked ? el("button", { class: "small", onclick: () => save(it, { ats: c.ats_pick, ou: c.ou_pick }) }, "Use Claude") : null),
      el("td", {}, res(it.ats_result), " ", res(it.ou_result),
        (it.claude_ats_result || it.claude_ou_result) ? el("div", { class: "hint" }, "Claude ", res(it.claude_ats_result), " ", res(it.claude_ou_result)) : null),
    );
  });

  const predictAll = el("button", {}, isManual() ? "Copy whole card for Claude" : "Predict all (Claude, pool lines)");
  if (isManual()) predictAll.addEventListener("click", () => claudeModal({
    title: `Claude: week ${data.card.week} card`,
    getPrompt: () => api(`/api/pool/cards/${data.card.id}/prompt`),
    submit: (answer) => api(`/api/pool/cards/${data.card.id}/answer`, { method: "POST", body: { answer } }),
    done: async (r) => { status(`Saved Claude's picks for ${r.saved.length} item(s).`); await loadPool(); },
  }));
  else predictAll.addEventListener("click", () => withBusy(predictAll, "Predicting…", async () => {
    const todo = items.filter((it) => (data.card.locked || !it.locked) && it.data.game_id != null);
    for (const [i, it] of todo.entries()) {
      status(`Claude: ${i + 1}/${todo.length}…`);
      try { await api(`/api/pool/items/${it.id}/predict`, { method: "POST", body: {} }); }
      catch (e) { status(`${e.message} (item ${i + 1})`, true); }
    }
    status("Predictions done.");
    await loadPool();
  }));
  const useAll = el("button", {}, "Use all Claude picks");
  useAll.addEventListener("click", async () => {
    for (const it of items) {
      if (!it.claude || it.locked) continue;
      const body = it.kind === "prop" ? { pick: it.claude.pick } : { ats: it.claude.ats_pick, ou: it.claude.ou_pick };
      await api(`/api/pool/items/${it.id}/pick`, { method: "POST", body });
    }
    await loadPool();
  });
  const reviewBtn = el("button", {}, isManual() ? "Copy review for Claude (after grading)" : "Run Claude's review (after grading)");
  if (isManual()) reviewBtn.addEventListener("click", () => claudeModal({
    title: `Claude: review week ${data.card.week}`,
    getPrompt: () => api(`/api/pool/cards/${data.card.id}/review/prompt`),
    submit: (answer) => api(`/api/pool/cards/${data.card.id}/review/answer`, { method: "POST", body: { answer } }),
    done: (r) => status(`Review saved: ${r.lessons.length} lesson(s). See the Review tab.`),
  }));
  else reviewBtn.addEventListener("click", () => withBusy(reviewBtn, "Reviewing…", async () => {
    const r = await api(`/api/pool/cards/${data.card.id}/review`, { method: "POST", body: {} });
    status(`Review done: ${r.lessons.length} lesson(s). See the Review tab.`);
  }));
  const gradeBtn = el("button", {}, "Grade card");
  gradeBtn.addEventListener("click", () => withBusy(gradeBtn, "Grading…", async () => {
    const r = await api(`/api/pool/cards/${data.card.id}/grade`, { method: "POST", body: {} });
    status(`Graded ${r.graded} items.`);
    await loadPool();
  }));

  const tbInput = el("input", { type: "number", value: data.card.tiebreaker ?? data.projected_combined ?? "" });
  const tbSave = el("button", { class: "small" }, "Save tiebreaker");
  tbSave.addEventListener("click", async () => {
    await api(`/api/pool/cards/${data.card.id}/tiebreaker`, { method: "POST", body: { value: tbInput.value } });
    await loadPool();
  });

  const exportBox = el("textarea", { class: "export", readonly: true });
  exportBox.value = data.export;
  const copyBtn = el("button", { class: "small" }, "Copy picks");
  copyBtn.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(exportBox.value); status("Copied. Paste into your reply."); }
    catch { exportBox.select(); status("Select-all done; copy with Ctrl/Cmd+C."); }
  });

  const pad = (n) => String(n).padStart(2, "0");
  const toLocalInput = (iso) => { const d = new Date(iso); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`; };
  const dl = data.card.deadline ? new Date(data.card.deadline) : null;
  const dlIn = el("input", { type: "datetime-local", value: dl ? toLocalInput(data.card.deadline) : "" });
  const dlSave = el("button", { class: "small" }, "Save deadline");
  dlSave.addEventListener("click", async () => {
    await api(`/api/pool/cards/${data.card.id}/deadline`, { method: "POST", body: { deadline: dlIn.value ? new Date(dlIn.value).toISOString() : "" } });
    await loadPool();
  });
  let dlText = "No deadline set";
  if (dl) {
    const ms = dl - new Date();
    const when = dl.toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
    dlText = ms <= 0 ? `Picks locked (deadline ${when})` :
      `Picks due ${when} (in ${Math.floor(ms / 86400000)}d ${Math.floor(ms / 3600000) % 24}h ${Math.floor(ms / 60000) % 60}m)`;
  }
  const deadlineBar = el("div", { class: `check ${data.card.locked ? "bad" : ""}` }, el("strong", {}, dlText), " ",
    data.card.locked ? null : [dlIn, " ", dlSave]);

  const addForms = data.card.locked ? null : (() => {
    const slate = state.games.length ? state.games : [];
    const gameSel = el("select", {}, el("option", { value: "" }, "Choose a game from this week's slate…"),
      slate.map((g) => el("option", { value: g.id }, `${g.away_team} @ ${g.home_team}`)));
    const sIn = el("input", { type: "number", step: "0.5", placeholder: "home spread", class: "line-in" });
    const tIn = el("input", { type: "number", step: "0.5", placeholder: "o/u", class: "line-in" });
    const wIn = el("input", { type: "number", min: "1", value: "1", class: "line-in small-in", title: "Points per correct pick" });
    const kindSel = el("select", {}, el("option", { value: "game" }, "Game"), el("option", { value: "mid_major" }, "Mid-major (spread only)"));
    const addGame = el("button", { class: "small" }, "Add game");
    addGame.addEventListener("click", async () => {
      try {
        await api(`/api/pool/cards/${data.card.id}/items`, { method: "POST", body: { kind: kindSel.value, game_id: Number(gameSel.value), home_spread: sIn.value, total: tIn.value, weight: wIn.value } });
        await loadPool();
      } catch (e) { status(e.message, true); }
    });
    const qIn = el("input", { type: "text", placeholder: "Question, e.g. Will Northwestern rush for over or under 150.5 yards?", class: "q-in" });
    const qLine = el("input", { type: "number", step: "0.5", placeholder: "o/u", class: "line-in" });
    const addQ = el("button", { class: "small" }, "Add question");
    addQ.addEventListener("click", async () => {
      try { await api(`/api/pool/cards/${data.card.id}/items`, { method: "POST", body: { kind: "prop", question: qIn.value, line: qLine.value } }); await loadPool(); }
      catch (e) { status(e.message, true); }
    });
    return el("details", { open: items.length === 0 }, el("summary", {}, "Add games or questions by hand"),
      slate.length ? null : el("p", { class: "warn" }, "Load or sync this week on the Slate tab first so games can be chosen."),
      el("div", { class: "pool-actions" }, gameSel, kindSel, "Home spread", sIn, "O/U", tIn, "pts", wIn, addGame),
      el("div", { class: "pool-actions" }, qIn, "O/U", qLine, addQ));
  })();

  const sc = data.score;
  root.replaceChildren(
    el("h2", {}, `Pool · week ${data.card.week}`),
    deadlineBar,
    el("p", { class: "hint" }, sc.graded_possible ? `This week: ${sc.points} pts (${sc.correct}/${sc.graded_possible} correct) · Claude ${sc.claude_points} pts. ` : "", data.scoring_note),
    data.card.locked ? el("p", { class: "hint" }, "Deadline passed: your picks are locked. Claude can still run a strict replay (only information from before the deadline) for testing.") : "",
    el("div", { class: "pool-actions" }, predictAll, useAll, gradeBtn, reviewBtn),
    el("div", { class: "table-wrap" }, el("table", { class: "pool-table" },
      el("thead", {}, el("tr", {}, ["", "Game & pool line", "Claude (vs pool line)", "My pick", "Result"].map((h) => el("th", {}, h)))),
      el("tbody", {}, rows))),
    addForms || "",
    !data.card.has_tiebreaker ? "" : el("div", { class: "pool-actions" }, "Tiebreaker (combined score):", tbInput, tbSave,
      data.projected_combined ? el("span", { class: "hint" }, `Claude's projected total of all card games: ${data.projected_combined}`) : null),
    el("h2", {}, "Reply text"), exportBox, el("div", { class: "pool-actions" }, copyBtn),
    importSection,
  );
}

// ---------- league ----------

let leagueView = "week";

async function loadLeague() {
  const p = weekParams();
  const root = $("#league");
  const lb = await api(`/api/league/leaderboard?season=${p.season}`);
  const cfg = lb.scoring;
  const hl = await api("/api/league/highlights");
  const hlClass = (h) => (h ? `hl-${h}` : "");

  const nav = el("div", { class: "subnav" }, [["week", `Weekly sheet · week ${p.week}`], ["season", `Season totals ${p.season}`], ["add", "Add picks"], ["rules", "Scoring & highlights"]]
    .map(([v, label]) => el("button", { class: `small ${leagueView === v ? "active" : ""}`, onclick: () => { leagueView = v; loadLeague(); } }, label)));

  let body;
  if (leagueView === "week") {
    let sh = null, err = null;
    try { sh = await api(`/api/league/sheet/week?${new URLSearchParams(p)}`); } catch (e) { err = e.message; }
    if (err) body = el("p", { class: "warn" }, err);
    else if (!sh.rows.length) body = el("p", { class: "empty" }, "No picks entered for this week yet. Use “Add picks”.");
    else {
      const resCls = (r) => (r === "W" ? "res-w" : r === "L" ? "res-l" : r === "P" ? "res-p" : "");
      body = el("div", {},
        el("div", { class: "pool-actions" },
          el("a", { class: "button-link", href: `/api/league/sheet/week?${new URLSearchParams({ ...p, format: "csv" })}` }, "Download CSV"),
          el("span", { class: "hint" }, sh.actual_combined != null ? `Actual combined score (card games, mid-major excluded): ${sh.actual_combined}` : "Combined score appears once every card game is final.")),
        el("div", { class: "table-wrap sheet-wrap" }, el("table", { class: "sheet" },
          el("thead", {}, el("tr", {}, el("th", {}, "#"), el("th", { class: "sticky" }, "Player"),
            ...sh.columns.map((c) => el("th", { title: c.line }, c.kind === "prop" ? c.label : c.label.replace(" @ ", " @\n"),
              el("div", { class: "hint" }, c.line + (c.weight > 1 ? ` · ${c.weight} pts` : "") + (c.kind === "mid_major" ? " · mid-major" : "")))),
            ...["Spread", "O/U", "Bonus", "Qs", "Mid", "Combined (off)", "Comb pts", "POTW", "Total"].map((h) => el("th", { class: "num" }, h)))),
          el("tbody", {}, sh.rows.map((r, i) => el("tr", { class: hlClass(r.highlight) },
            el("td", {}, i + 1), el("td", { class: "sticky" }, el("strong", {}, r.player)),
            ...r.cells.map((c) => el("td", { class: "pick-cell" }, c.label ? [
              el("span", { class: resCls(c.ats_result) }, c.label.split(", ")[0]),
              c.label.includes(", ") ? [", ", el("span", { class: resCls(c.ou_result) }, c.label.split(", ")[1])] : null] : "—")),
            el("td", { class: "num" }, r.breakdown.ats), el("td", { class: "num" }, r.breakdown.ou), el("td", { class: "num" }, r.breakdown.bonus_game),
            el("td", { class: "num" }, r.breakdown.prop), el("td", { class: "num" }, r.breakdown.mid_major),
            el("td", { class: "num" }, r.tiebreaker ?? "—", r.distance != null ? ` (${r.distance})` : ""),
            el("td", { class: "num" }, r.tiebreak_points), el("td", { class: "num" }, r.potw ? el("span", { class: "potw" }, "★") : ""),
            el("td", { class: "num" }, el("strong", {}, r.total))))))),
        el("p", { class: "hint" }, "Green = correct, red = wrong, yellow = push. Picks shown as team (spread) and O/U."));
    }
  } else if (leagueView === "season") {
    const sh = await api(`/api/league/sheet/season?season=${p.season}`);
    body = !sh.rows.length ? el("p", { class: "empty" }, "No entries yet.") : el("div", {},
      el("div", { class: "pool-actions" }, el("a", { class: "button-link", href: `/api/league/sheet/season?season=${p.season}&format=csv` }, "Download CSV")),
      el("div", { class: "table-wrap sheet-wrap" }, el("table", { class: "sheet" },
        el("thead", {}, el("tr", {}, ["Rank", "Player", ...sh.weeks.map((w) => `Wk ${w}`), "Total", "Behind", "POTW", "Pick %"]
          .map((h, i) => el("th", { class: i === 1 ? "sticky" : "num" }, h)))),
        el("tbody", {}, sh.rows.map((r) => el("tr", { class: hlClass(r.highlight) },
          el("td", { class: "num" }, r.rank), el("td", { class: "sticky" }, el("strong", {}, r.player)),
          ...r.weeks_cells.map((v) => el("td", { class: "num" }, v ?? "—")),
          el("td", { class: "num" }, el("strong", {}, r.total)), el("td", { class: "num" }, r.behind ? `-${r.behind}` : "—"),
          el("td", { class: "num" }, r.potw ? `★ ${r.potw}` : ""), el("td", { class: "num" }, r.pick_pct != null ? `${r.pick_pct}%` : "—")))))),
      lb.claude_total ? el("p", { class: "hint" }, `Claude (not in league) would have ${lb.claude_total} points on weeks it predicted.`) : "");
  } else if (leagueView === "add") {
    const players = [...new Set(lb.standings.map((s) => s.player))];
    const nameIn = el("input", { type: "text", placeholder: "Player name", list: "playerNames" });
    const ptsIn = el("input", { type: "number", step: "1", placeholder: "League's posted points (optional)" });
    const textIn = el("textarea", { placeholder: "Paste this player's picks for the week (same format as the card, with their picks)…" });
    const addBtn = el("button", { class: "primary" }, "Add / replace picks");
    addBtn.addEventListener("click", () => withBusy(addBtn, "Saving…", async () => {
      const r = await api("/api/league/entries", { method: "POST", body: { ...p, name: nameIn.value, text: textIn.value, reported_points: ptsIn.value } });
      status(`${r.player}: ${r.picks} picks, combined-score guess ${r.tiebreaker ?? "—"}.` + (r.warnings.length ? ` Check: ${r.warnings.join("; ")}` : ""), r.warnings.length > 0);
      textIn.value = ""; ptsIn.value = ""; nameIn.value = "";
    }));
    let entryList = "";
    try {
      const w = await api(`/api/league/week?${new URLSearchParams(p)}`);
      entryList = el("div", { class: "table-wrap" }, el("table", { class: "league-table" },
        el("thead", {}, el("tr", {}, ["Player", "Combined guess", "League's posted points", "Ours", "Notes", ""].map((h) => el("th", {}, h)))),
        el("tbody", {}, w.rows.map((r) => {
          const posted = el("input", { type: "number", value: r.reported_points ?? "", class: "line-in" });
          posted.addEventListener("change", async () => { await api(`/api/league/entries/${r.entry_id}`, { method: "POST", body: { reported_points: posted.value } }); loadLeague(); });
          const diff = r.reported_points != null ? r.total - r.reported_points : null;
          return el("tr", { class: hlClass(highlightOf(r.player, hl)) }, el("td", {}, r.player), el("td", {}, r.tiebreaker ?? "—"), el("td", {}, posted),
            el("td", {}, r.total, diff != null ? el("span", { class: diff === 0 ? "diff-ok" : "diff-bad" }, diff === 0 ? " ✓" : ` (${diff > 0 ? "+" : ""}${diff})`) : ""),
            el("td", { class: "warn", style: "white-space:normal" }, (r.warnings || []).join("; ")),
            el("td", {}, el("button", { class: "small", onclick: async () => { if (confirm(`Remove ${r.player}'s picks for this week?`)) { await api(`/api/league/entries/${r.entry_id}`, { method: "DELETE" }); loadLeague(); } } }, "✕")));
        }))));
    } catch (e) { entryList = el("p", { class: "warn" }, e.message); }
    body = el("div", {},
      el("datalist", { id: "playerNames" }, players.map((n) => el("option", { value: n }))),
      el("div", { class: "league-add" }, el("div", {}, nameIn, el("div", { style: "height:6px" }), ptsIn), textIn),
      el("div", { class: "pool-actions" }, addBtn),
      el("h3", {}, `Entered for week ${p.week}`), entryList);
  } else {
    // Scoring + highlights + rule inference
    const num = (k, label) => { const i = el("input", { type: "number", value: cfg[k] }); i.dataset.k = k; return el("label", {}, label, i); };
    const tbType = el("select", {}, ["none", "closest", "exact", "within", "tiers"].map((t) => el("option", { value: t, selected: cfg.tiebreak.type === t }, t)));
    const tbPts = el("input", { type: "text", value: Array.isArray(cfg.tiebreak.points) ? cfg.tiebreak.points.join(",") : (cfg.tiebreak.points ?? "") });
    const tbMargin = el("input", { type: "number", value: cfg.tiebreak.margin ?? "" });
    const bonusMode = el("select", {}, [["ats", "spread pick worth N"], ["both", "spread and O/U worth N"], ["ats_plus", "spread pick worth 3 + N"]].map(([v, l]) => el("option", { value: v, selected: cfg.bonus_game_mode === v }, l)));
    const potwTies = el("select", {}, [["all", "all tied get it"], ["closest_combined", "closest combined guess"]].map(([v, l]) => el("option", { value: v, selected: cfg.potw_ties === v }, l)));
    const fields = [num("ats", "Spread"), num("ou", "O/U"), num("prop", "Question"), num("mid_major", "Mid-major"), num("potw_bonus", "POTW bonus")];
    const saveCfg = el("button", { class: "small" }, "Save scoring");
    saveCfg.addEventListener("click", async () => {
      const b = { bonus_game_mode: bonusMode.value, potw_ties: potwTies.value, tiebreak: { type: tbType.value } };
      fields.forEach((f) => { const i = f.querySelector("input"); b[i.dataset.k] = Number(i.value); });
      if (tbType.value === "tiers") b.tiebreak.points = tbPts.value.split(",").map(Number).filter((x) => !isNaN(x));
      else if (tbType.value !== "none") b.tiebreak.points = Number(tbPts.value || 1);
      if (tbType.value === "within") b.tiebreak.margin = Number(tbMargin.value || 5);
      await api("/api/league/scoring", { method: "POST", body: b });
      status("Scoring saved."); loadLeague();
    });

    const hlRows = Object.entries(hl).map(([name, color]) => ({ name, color }));
    hlRows.push({ name: "", color: "red" });
    const hlInputs = hlRows.map((r) => {
      const n = el("input", { type: "text", value: r.name, placeholder: "Player name" });
      const c = el("select", {}, [["red", "Light red"], ["gold", "Gold"], ["green", "Light green"], ["blue", "Light blue"]].map(([v, l]) => el("option", { value: v, selected: r.color === v }, l)));
      return { n, c, row: el("div", { class: "pool-actions" }, n, c) };
    });
    const saveHl = el("button", { class: "small" }, "Save highlights");
    saveHl.addEventListener("click", async () => {
      const out = {};
      hlInputs.forEach(({ n, c }) => { if (n.value.trim()) out[n.value.trim()] = c.value; });
      await api("/api/league/highlights", { method: "POST", body: out });
      status("Highlights saved."); loadLeague();
    });

    const inferBox = el("div", {});
    const inferBtn = el("button", { class: "small" }, "Figure out the unknown rules from posted points");
    inferBtn.addEventListener("click", () => withBusy(inferBtn, "Analyzing…", async () => {
      const r = await api(`/api/league/infer?season=${p.season}`);
      inferBox.replaceChildren(el("p", {}, r.message || ""),
        r.candidates.length ? el("div", { class: "table-wrap" }, el("table", {},
          el("thead", {}, el("tr", {}, ["Rule set", "Matches", "Avg error", ""].map((h) => el("th", {}, h)))),
          el("tbody", {}, r.candidates.map((c) => el("tr", {}, el("td", { style: "white-space:normal" }, c.rule), el("td", {}, `${c.exact}/${c.of} (${c.match_pct}%)`),
            el("td", {}, c.avg_abs_error), el("td", {}, el("button", { class: "small", onclick: async () => { await api("/api/league/scoring", { method: "POST", body: c.config }); status("Applied."); loadLeague(); } }, "Use"))))))) : "",
        r.residuals && r.residuals.length ? el("details", {}, el("summary", {}, "Unexplained points under current rules"),
          el("div", { class: "table-wrap" }, el("table", {}, el("thead", {}, el("tr", {}, ["Week", "Player", "Posted", "Ours", "Unexplained", "Guess", "Off by", "POTW"].map((h) => el("th", {}, h)))),
            el("tbody", {}, r.residuals.map((x) => el("tr", {}, el("td", {}, x.week), el("td", {}, x.player), el("td", {}, x.reported), el("td", {}, x.computed),
              el("td", {}, x.unexplained), el("td", {}, x.guess ?? "—"), el("td", {}, x.distance ?? "—"), el("td", {}, x.potw ? "★" : ""))))))) : "");
    }));

    body = el("div", {},
      el("h3", {}, "Scoring"),
      el("p", { class: "hint" }, `Spread ${cfg.ats}, O/U ${cfg.ou}, question ${cfg.prop}, mid-major ${cfg.mid_major}, POTW +${cfg.potw_bonus}. Combined score: ${cfg.tiebreak.type}.`),
      el("div", { class: "scoring-form" }, ...fields,
        el("label", {}, "N-Point Game", bonusMode), el("label", {}, "Combined score", tbType), el("label", {}, "points (tiers: 3,2,1)", tbPts),
        el("label", {}, "within N", tbMargin), el("label", {}, "POTW ties", potwTies), saveCfg),
      el("h3", {}, "Figure out the unknown rules"),
      el("p", { class: "hint" }, "Enter the league's posted points for past weeks (Add picks), then run this to pin down the combined-score, 5-point-game and POTW tie rules."),
      el("div", { class: "pool-actions" }, inferBtn), inferBox,
      el("h3", {}, "Row highlights"),
      el("p", { class: "hint" }, "Names must match how players are entered (capitalization and curly apostrophes don't matter)."),
      ...hlInputs.map((x) => x.row), el("div", { class: "pool-actions" }, saveHl));
  }
  root.replaceChildren(el("h2", {}, "League"), nav, body);
}

function highlightOf(name, hl) {
  const n = (s) => (s || "").replace(/[‘’`]/g, "'").toLowerCase().split(/\s+/).filter(Boolean).join(" ");
  const want = n(name);
  for (const [k, v] of Object.entries(hl || {})) if (n(k) === want) return v;
  return null;
}

// ---------- review ----------

function recCell(r) { return r && (r.w + r.l + r.p) ? `${r.w}-${r.l}${r.p ? `-${r.p}` : ""}${r.pct != null ? ` (${r.pct}%)` : ""}` : "—"; }

async function loadReview() {
  const season = Number($("#season").value) || new Date().getFullYear();
  const r = await api(`/api/review?season=${season}`);
  const s = r.season_stats;
  const lessonRows = r.lessons.map((l) => {
    const cb = el("input", { type: "checkbox", checked: !!l.active });
    cb.addEventListener("change", async () => { await api(`/api/lessons/${l.id}`, { method: "POST", body: { active: cb.checked } }); loadReview(); });
    const del = el("button", { class: "small" }, "✕");
    del.addEventListener("click", async () => { await api(`/api/lessons/${l.id}`, { method: "DELETE" }); loadReview(); });
    return el("div", { class: `lesson-row ${l.active ? "" : "off"}` }, cb,
      el("div", {}, el("strong", {}, `Week ${l.week} · ${l.applies_to}: `), l.text, el("div", { class: "hint" }, l.evidence)), del);
  });
  $("#review").replaceChildren(
    el("h2", {}, `Claude's self-review · ${season}`),
    el("p", { class: "hint" }, "Lessons that are switched on, plus the calibration facts below, go into the next weeks' prompts (never into replays of earlier weeks)."),
    el("div", { class: "stat-grid" },
      el("div", { class: "stat" }, el("h4", {}, "Claude overall"), el("div", { class: "line" }, el("span", {}, "All picks"), el("span", {}, recCell(s.overall))),
        ...Object.entries(s.by_type || {}).map(([k, v]) => el("div", { class: "line" }, el("span", {}, k.toUpperCase()), el("span", {}, recCell(v))))),
      el("div", { class: "stat" }, el("h4", {}, "Me vs Claude"), el("div", { class: "line" }, el("span", {}, "Me"), el("span", {}, recCell(s.me))),
        s.agreement ? el("div", { class: "line" }, el("span", {}, "When we agree"), el("span", {}, recCell(s.agreement.when_agree))) : null,
        s.agreement ? el("div", { class: "line" }, el("span", {}, "Disagree: Claude"), el("span", {}, recCell(s.agreement.claude_when_disagree))) : null,
        s.agreement ? el("div", { class: "line" }, el("span", {}, "Disagree: me"), el("span", {}, recCell(s.agreement.me_when_disagree))) : null),
      el("div", { class: "stat" }, el("h4", {}, "Favorites / underdogs (spread)"),
        el("div", { class: "line" }, el("span", {}, "Favorites"), el("span", {}, recCell(s.favorites))),
        el("div", { class: "line" }, el("span", {}, "Underdogs"), el("span", {}, recCell(s.underdogs)))),
      s.closing_line_value ? el("div", { class: "stat" }, el("h4", {}, "Closing-line value"),
        el("div", { class: "line" }, el("span", {}, "Beat the close"), el("span", {}, `${s.closing_line_value.beat_close_pct}%`)),
        el("div", { class: "line" }, el("span", {}, "Avg points"), el("span", {}, `${s.closing_line_value.avg_points}`)),
        el("div", { class: "line" }, el("span", {}, "With market move"), el("span", {}, recCell(s.closing_line_value.with_market_move))),
        el("div", { class: "line" }, el("span", {}, "Against market move"), el("span", {}, recCell(s.closing_line_value.against_market_move)))) : null,
    ),
    el("h2", {}, "Confidence calibration"),
    s.calibration && s.calibration.length ? el("div", { class: "table-wrap" }, el("table", {},
      el("thead", {}, el("tr", {}, ["Confidence", "Expected", "Actual", "Record"].map((h) => el("th", {}, h)))),
      el("tbody", {}, s.calibration.map((b) => el("tr", {}, el("td", {}, b.range), el("td", {}, `${b.expected}%`), el("td", {}, b.pct != null ? `${b.pct}%` : "—"), el("td", {}, recCell(b))))))) : el("p", { class: "empty" }, "No graded picks yet."),
    el("h2", {}, "Lessons"), lessonRows.length ? el("div", {}, lessonRows) : el("p", { class: "empty" }, "No lessons yet. Grade a pool card, then run Claude's review."),
    el("h2", {}, "Weeks"),
    ...r.weeks.map((w) => el("details", { open: w === r.weeks[r.weeks.length - 1] },
      el("summary", {}, `Week ${w.week}: Claude ${recCell(w.stats.overall)} · Me ${recCell(w.stats.me)}`),
      w.review ? [el("p", {}, w.review.summary), el("p", { class: "hint" }, w.review.calibration_note),
        el("div", { class: "table-wrap" }, el("table", {}, el("thead", {}, el("tr", {}, ["#", "Type", "Verdict", "Cause", "What happened", "Lesson"].map((h) => el("th", {}, h)))),
          el("tbody", {}, w.review.items.map((it) => el("tr", {}, el("td", {}, it.idx + 1), el("td", {}, it.type),
            el("td", {}, el("span", { class: `verdict ${it.verdict}` }, it.verdict.replace("_", " "))), el("td", {}, it.miss_category.replaceAll("_", " ")),
            el("td", { style: "white-space:normal" }, it.what_happened), el("td", { style: "white-space:normal" }, it.lesson))))))]
        : el("p", { class: "hint" }, "Not reviewed yet: open the Pool tab for this week and click “Run Claude's review”."))),
  );
}

// ---------- teams ----------

async function loadTeams() {
  const season = Number($("#season").value) || new Date().getFullYear();
  const r = await api(`/api/teams?season=${season}`);
  const c = r.check || {};
  const diffs = [];
  if (c.missing && c.missing.length) diffs.push(`Missing from CFBD: ${c.missing.join(", ")}`);
  if (c.unexpected && c.unexpected.length) diffs.push(`Not in verified list: ${c.unexpected.join(", ")}`);
  for (const m of c.conference_mismatches || []) diffs.push(`${m.team}: CFBD says ${m.cfbd}, verified list says ${m.reference}`);
  $("#teams").replaceChildren(
    el("h2", {}, `${season} FBS teams (${r.count})`),
    el("div", { class: `check ${c.checked ? (c.ok ? "ok" : "bad") : ""}` }, c.message || "",
      diffs.length ? el("ul", {}, diffs.map((d) => el("li", {}, d))) : null),
    el("div", { class: "conf-grid" }, Object.entries(r.conferences).map(([conf, ts]) =>
      el("div", { class: "conf" }, el("h3", {}, `${conf} (${ts.length})`),
        el("ul", {}, ts.map((t) => el("li", {}, t.school, t.note ? el("span", { class: "note" }, t.note) : null)))))),
    r.sources.length ? el("p", { class: "hint" }, "Verified against: ",
      ...r.sources.map((u, i) => [i ? ", " : "", el("a", { href: u, target: "_blank", rel: "noopener noreferrer" }, new URL(u).hostname)])) : "",
  );
}

// ---------- wiring ----------

// ---------- copy-for-Claude (no API key) ----------

const isManual = () => state.claudeMode === "manual";

// Step 1: copy the prompt into a Claude chat. Step 2: paste Claude's reply back and save.
async function claudeModal({ title, getPrompt, submit, done }) {
  const promptBox = el("textarea", { class: "cm-prompt", readonly: true, rows: 10 }, "Building the prompt…");
  const answerBox = el("textarea", { class: "cm-answer", rows: 8, placeholder: "Paste Claude's whole reply here (the JSON block is enough)." });
  const msg = el("div", { class: "cm-msg", role: "status" });
  const close = () => overlay.remove();
  const copyBtn = el("button", { disabled: true }, "Copy prompt");
  copyBtn.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(promptBox.value); }
    catch { promptBox.select(); document.execCommand("copy"); }
    copyBtn.textContent = "Copied ✓";
  });
  const saveBtn = el("button", { class: "primary" }, "Save Claude's answer");
  saveBtn.addEventListener("click", () => withBusy(saveBtn, "Saving…", async () => {
    msg.classList.remove("error");
    try {
      const r = await submit(answerBox.value);
      const errs = (r && r.errors) || [];
      if (errs.length) { msg.classList.add("error"); msg.replaceChildren(el("strong", {}, "Saved, with problems:"), el("ul", {}, errs.map((e) => el("li", {}, e)))); }
      else close();
      if (done) await done(r);
    } catch (e) { msg.classList.add("error"); msg.textContent = e.message; }
  }));
  const overlay = el("div", { class: "cm-overlay", onclick: (e) => { if (e.target === overlay) close(); } },
    el("div", { class: "cm", role: "dialog", "aria-label": title },
      el("div", { class: "cm-head" }, el("h3", {}, title), el("button", { class: "small", onclick: close, title: "Close" }, "✕")),
      el("p", { class: "hint" }, "1. Copy the prompt and paste it into a new chat at ",
        el("a", { href: "https://claude.ai/new", target: "_blank", rel: "noopener" }, "claude.ai"),
        " (turn on web search so Claude can check injuries and coaching news)."),
      promptBox, el("div", { class: "cm-row" }, copyBtn),
      el("p", { class: "hint" }, "2. When Claude answers, copy its reply and paste it below."),
      answerBox, msg, el("div", { class: "cm-row" }, saveBtn)));
  document.body.append(overlay);
  try {
    const r = await getPrompt();
    promptBox.value = r.prompt;
    copyBtn.disabled = false;
    if (r.replay) msg.textContent = `Blind replay: information cutoff ${new Date(r.as_of).toLocaleString()}.`;
  } catch (e) { promptBox.value = ""; msg.classList.add("error"); msg.textContent = e.message; copyBtn.disabled = true; }
}

async function withBusy(btn, label, fn) {
  const orig = btn.textContent;
  btn.disabled = true;
  btn.textContent = label;
  try { await fn(); } catch (e) { status(e.message, true); } finally {
    btn.disabled = false;
    btn.textContent = orig;
  }
}

$("#loadBtn").addEventListener("click", () => withBusy($("#loadBtn"), "Loading…", async () => {
  status("");
  await loadGames();
  if (!$("#record").classList.contains("hidden")) await loadRecord();
}));

$("#syncBtn").addEventListener("click", () => withBusy($("#syncBtn"), "Syncing…", async () => {
  const r = await api("/api/sync", { method: "POST", body: weekParams() });
  await loadGames();
  const msg = `Synced ${r.games} games (${r.with_lines} with lines, ${r.forecasts} forecasts). Graded ${r.graded}.`;
  status(r.warnings && r.warnings.length ? `${msg} Warnings: ${r.warnings.join(" | ")}` : msg, Boolean(r.warnings && r.warnings.length));
}));

$("#gradeBtn").addEventListener("click", () => withBusy($("#gradeBtn"), "Grading…", async () => {
  const r = await api("/api/grade", { method: "POST", body: weekParams() });
  await loadGames();
  status(`${r.completed} games final. Graded ${r.graded} of your picks.`);
}));

// Theme toggle: dark (default) -> light -> auto (system)
(function themeToggle() {
  const btn = $("#themeToggle");
  const labels = { dark: "🌙 Dark", light: "☀️ Light", auto: "🖥 Auto" };
  const next = { dark: "light", light: "auto", auto: "dark" };
  const show = () => { btn.textContent = labels[document.documentElement.dataset.theme] || labels.dark; };
  btn.addEventListener("click", () => {
    const t = next[document.documentElement.dataset.theme] || "dark";
    document.documentElement.dataset.theme = t;
    try { localStorage.setItem("cfbPicks.theme", t); } catch {}
    show();
  });
  show();
})();

$("#onlyLines").addEventListener("change", renderSlate);
$("#search").addEventListener("input", renderSlate);

document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", async () => {
  document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === t));
  const tab = t.dataset.tab;
  $("#slate").classList.toggle("hidden", tab !== "slate");
  $("#record").classList.toggle("hidden", tab !== "record");
  $("#teams").classList.toggle("hidden", tab !== "teams");
  $("#pool").classList.toggle("hidden", tab !== "pool");
  $("#review").classList.toggle("hidden", tab !== "review");
  $("#league").classList.toggle("hidden", tab !== "league");
  try {
    if (tab === "league") await loadLeague();
    if (tab === "review") await loadReview();
    if (tab === "pool") await loadPool();
    if (tab === "record") await loadRecord();
    if (tab === "teams") await loadTeams();
  } catch (e) { status(e.message, true); }
}));

(async function init() {
  try { state.claudeMode = (await api("/api/config")).claude_mode; } catch { state.claudeMode = "manual"; }
  if (isManual()) {  // the chat does its own injury / intel searches
    for (const id of ["#searchInjuries", "#searchIntel"]) $(id).closest("label").classList.add("hidden");
  }
  let saved = null;
  try { saved = JSON.parse(localStorage.getItem("cfbPicks.controls")); } catch {}
  const year = new Date().getFullYear();
  $("#season").value = saved?.season || year;
  $("#seasonType").value = saved?.season_type || "regular";
  if (saved?.week) $("#week").value = saved.week;

  try {
    const cw = await api(`/api/current-week?season=${$("#season").value}`);
    if (cw.week) {
      $("#week").value = cw.week;
      $("#seasonType").value = cw.season_type;
    } else if (cw.warning) {
      status(cw.warning, true);
    }
  } catch {}
  if ($("#week").value) {
    try { await loadGames(); } catch (e) { status(e.message, true); }
  }
})();
