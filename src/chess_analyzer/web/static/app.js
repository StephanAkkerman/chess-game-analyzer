// Chess Analyzer front end. No build step: plain ES modules served as-is.
// All text from the API (names, PGN headers) goes through textContent.

const view = document.getElementById("view");
const store = {
  get(key, fallback = null) {
    try {
      const value = localStorage.getItem(key);
      return value === null ? fallback : JSON.parse(value);
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      /* private mode: nothing is remembered */
    }
  },
};

const CLASS_INFO = {
  book: { label: "Book", sym: "" },
  forced: { label: "Forced", sym: "" },
  best: { label: "Best", sym: "★" },
  good: { label: "Good", sym: "" },
  inaccuracy: { label: "Inaccuracy", sym: "?!" },
  mistake: { label: "Mistake", sym: "?" },
  blunder: { label: "Blunder", sym: "??" },
};
// What kind of error a mistake or blunder was. `who` is the side that
// replies, as "You", "Your opponent", "White" or "Black".
const CATEGORY_INFO = {
  allowed_mate: {
    label: "Allowed mate",
    text: (m, who) => `${who} can force mate${m.reply_san ? `, starting with ${m.reply_san}` : ""}.`,
  },
  missed_mate: { label: "Missed mate", text: (m) => `${m.best_san} started a forced mate.` },
  hung_piece: { label: "Hung a piece", text: (m, who) => `${who} can win a piece with ${m.reply_san}.` },
  refuted_attack: {
    label: "Unsound attack",
    text: (m) => `The attack doesn't work: ${m.reply_san} wins the piece.`,
  },
  allowed_tactic: { label: "Allowed a tactic", text: (m, who) => `${who} has a strong reply: ${m.reply_san}.` },
  conversion: { label: "Spoiled a won endgame", text: () => "This endgame was winning, but not any more." },
  missed_tactic: { label: "Missed tactic", text: (m) => `The forcing ${m.best_san} was much stronger.` },
  positional: { label: "Positional", text: () => "No immediate tactic: a strategic error." },
};
const TIME_FLAG_INFO = {
  impulsive: "Played in under 3 seconds: an impulsive move.",
  time_trouble: "Played with less than 10% of the clock left.",
  wasted_time: "A long think on an obvious move.",
};
const PHASE_LABELS = { opening: "Opening", middlegame: "Middlegame", endgame: "Endgame" };
const MATE = 10000;
let routeToken = 0;

// ---------------------------------------------------------------- helpers

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(child));
  }
  return node;
}

function svgEl(tag, attrs = {}) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}

function formatEval(cp, source) {
  if (cp === null || cp === undefined) return "Book";
  if (source === "tablebase") return cp > 0 ? "TB 1-0" : cp < 0 ? "TB 0-1" : "TB draw";
  if (Math.abs(cp) >= MATE - 500) {
    const moves = MATE - Math.abs(cp);
    const sign = cp > 0 ? "+" : "-";
    return moves ? `${sign}M${moves}` : `${sign}#`;
  }
  const pawns = cp / 100;
  return `${pawns >= 0 ? "+" : ""}${pawns.toFixed(1)}`;
}

// Lichess' win-probability curve: maps centipawns to -1..1.
function winChance(cp) {
  if (cp === null || cp === undefined) return 0;
  const capped = Math.max(-1500, Math.min(1500, cp));
  return 2 / (1 + Math.exp(-0.00368208 * capped)) - 1;
}

function formatSeconds(seconds) {
  if (seconds === null || seconds === undefined) return "";
  if (seconds < 60) return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes >= 60) return `${Math.floor(minutes / 60)}h${String(minutes % 60).padStart(2, "0")}`;
  return `${minutes}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
}

function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function sameName(a, b) {
  return Boolean(a && b) && a.toLowerCase() === b.toLowerCase();
}

function timeAgo(seconds) {
  const diff = Date.now() / 1000 - seconds;
  if (diff < 3600) return `${Math.max(1, Math.round(diff / 60))}m ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)}h ago`;
  if (diff < 86400 * 30) return `${Math.round(diff / 86400)}d ago`;
  return new Date(seconds * 1000).toLocaleDateString();
}

function formatTimeControl(tc) {
  if (!tc || tc === "-") return "";
  if (tc.includes("/")) return "daily";
  const [base, inc] = tc.split("+").map(Number);
  const minutes = base >= 60 ? `${base / 60}` : `${base}s`;
  return inc ? `${minutes}+${inc}` : `${minutes} min`;
}

// ---------------------------------------------------------------- API

class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  const code = store.get("accessCode");
  if (code) headers["X-Access-Code"] = code;
  if (options.body) headers["Content-Type"] = "application/json";
  const response = await fetch(path, { ...options, headers });
  if (response.status === 401) {
    await askAccessCode(Boolean(code));
    return api(path, options);
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new ApiError(response.status, data.detail || `Request failed (${response.status})`);
  }
  return data;
}

function askAccessCode(wasWrong) {
  const dialog = document.getElementById("access-dialog");
  const form = document.getElementById("access-form");
  const input = document.getElementById("access-input");
  const error = document.getElementById("access-error");
  error.hidden = !wasWrong;
  error.textContent = wasWrong ? "That code did not work." : "";
  input.value = "";
  return new Promise((resolve) => {
    form.onsubmit = () => {
      store.set("accessCode", input.value.trim());
      resolve();
    };
    dialog.showModal();
  });
}

let configPromise = null;
function getConfig() {
  configPromise ??= api("/api/config").catch(() => ({}));
  return configPromise;
}

// ---------------------------------------------------------------- recent

function rememberAnalysis(id, result) {
  const h = result.headers || {};
  const entry = {
    id,
    title: `${h.White || "?"} vs ${h.Black || "?"}`,
    date: h.Date || "",
    result: h.Result || "",
  };
  const recent = store.get("recent", []).filter((r) => r.id !== id);
  recent.unshift(entry);
  store.set("recent", recent.slice(0, 15));
}

// ---------------------------------------------------------------- home

function renderHome(initialUser) {
  view.replaceChildren(document.getElementById("home-template").content.cloneNode(true));
  const form = document.getElementById("user-form");
  const input = document.getElementById("username");
  const months = document.getElementById("months");
  const user = initialUser || store.get("username", "");
  input.value = user;
  months.value = String(store.get("months", 1));

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const name = input.value.trim();
    if (!name) return;
    store.set("username", name);
    store.set("months", Number(months.value));
    if (location.hash !== `#/u/${encodeURIComponent(name)}`) {
      location.hash = `#/u/${encodeURIComponent(name)}`;
    } else {
      loadGames(name, Number(months.value));
    }
  });
  months.addEventListener("change", () => {
    store.set("months", Number(months.value));
    if (input.value.trim()) loadGames(input.value.trim(), Number(months.value));
  });

  const pgnForm = document.getElementById("pgn-form");
  pgnForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const error = document.getElementById("pgn-error");
    const button = pgnForm.querySelector("button");
    error.hidden = true;
    button.disabled = true;
    try {
      const job = await api("/api/analyses", {
        method: "POST",
        body: JSON.stringify({ pgn: document.getElementById("pgn").value }),
      });
      location.hash = `#/a/${job.id}`;
    } catch (err) {
      error.textContent = err.message;
      error.hidden = false;
    } finally {
      button.disabled = false;
    }
  });

  renderRecent();
  getConfig().then((config) => {
    const info = document.getElementById("engine-info");
    if (!info || !config.engine) return;
    const parts = [`${config.engine_name || "Stockfish"}, ${config.engine}`];
    if (config.tablebases) parts.push("Syzygy tablebases");
    if (config.opening_source) parts.push(config.opening_source);
    info.textContent = parts.join(" · ");
  });

  if (initialUser) loadGames(initialUser, Number(months.value));
}

function renderRecent() {
  const recent = store.get("recent", []);
  const section = document.getElementById("recent");
  const list = document.getElementById("recent-list");
  if (!recent.length) return;
  section.hidden = false;
  list.replaceChildren(
    ...recent.map((r) =>
      el(
        "li",
        {},
        el(
          "a",
          { href: `#/a/${encodeURIComponent(r.id)}` },
          el("span", { text: r.title }),
          el("span", { class: "muted small", text: [r.result, r.date].filter(Boolean).join(" · ") }),
        ),
      ),
    ),
  );
}

async function loadGames(username, months) {
  const token = routeToken;
  const section = document.getElementById("games");
  const status = document.getElementById("games-status");
  const list = document.getElementById("game-list");
  section.hidden = false;
  document.getElementById("games-title").textContent = `Recent games of ${username}`;
  status.textContent = "Loading games from Chess.com…";
  list.replaceChildren();
  document.getElementById("analyse-all-row").hidden = true;
  loadStats(username);
  try {
    const data = await api(
      `/api/players/${encodeURIComponent(username)}/games?months=${months}&limit=40`,
    );
    if (token !== routeToken) return;
    status.textContent = data.games.length
      ? "Tap a game to analyse it."
      : "No games found in this period. Try a longer period.";
    list.replaceChildren(...data.games.map((game) => gameRow(game)));
    const row = document.getElementById("analyse-all-row");
    const button = document.getElementById("analyse-all");
    const count = Math.min(10, data.games.length);
    row.hidden = count < 2;
    button.textContent = `Analyse the ${count} most recent`;
    button.onclick = () => analyseRecent(username, data.games.slice(0, count));
  } catch (err) {
    if (token !== routeToken) return;
    status.textContent = err.message;
  }
}

// Queue several games, so the progress section has data to work with.
async function analyseRecent(username, games) {
  const token = routeToken;
  const button = document.getElementById("analyse-all");
  const status = document.getElementById("games-status");
  button.disabled = true;
  let sent = 0;
  let full = false;
  for (const game of games) {
    try {
      await api("/api/analyses", { method: "POST", body: JSON.stringify({ pgn: game.pgn }) });
      sent += 1;
    } catch (err) {
      if (err.status === 503) {
        full = true;
        break;
      }
    }
  }
  if (token !== routeToken) return;
  button.disabled = false;
  status.textContent =
    `Sent ${plural(sent, "game")} for analysis.` +
    (full ? " The queue is full; try the rest later." : "") +
    " Your progress updates as they finish.";
  // Refresh the progress section for a while as the analyses finish.
  for (let i = 0; i < 20; i++) {
    await sleep(30000);
    if (token !== routeToken) return;
    loadStats(username);
  }
}

async function loadStats(username) {
  const token = routeToken;
  const section = document.getElementById("progress");
  let stats;
  try {
    stats = await api(`/api/players/${encodeURIComponent(username)}/stats`);
  } catch {
    return;
  }
  if (token !== routeToken) return;
  section.hidden = false;
  const status = document.getElementById("progress-status");
  const body = document.getElementById("progress-body");
  if (!stats.games.length) {
    status.textContent =
      "Analyse a few games to see your accuracy over time and what to work on.";
    body.hidden = true;
    return;
  }
  status.textContent = `Based on ${plural(stats.games.length, "analysed game")}. Average centipawn loss: ${stats.acpl}.`;
  body.hidden = false;
  renderTrend(stats);
  document.getElementById("insights").replaceChildren(
    ...stats.insights.map((line) => el("li", { text: line })),
  );
  renderProgressDetails(stats);
}

// Average centipawn loss per game, oldest first. Lower is better.
function renderTrend(stats) {
  const svg = document.getElementById("trend");
  const games = stats.games;
  const width = Math.max(200, svg.clientWidth || 320);
  const height = 120;
  const pad = 10;
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  const top = Math.max(50, ...games.map((g) => g.acpl));
  const x = (i) => (games.length === 1 ? width / 2 : pad + ((width - 2 * pad) * i) / (games.length - 1));
  const y = (acpl) => height - pad - ((height - 2 * pad) * acpl) / top;
  const nodes = [
    svgEl("line", { class: "zero", x1: 0, x2: width, y1: y(0), y2: y(0) }),
    svgEl("line", { class: "average", x1: 0, x2: width, y1: y(stats.acpl), y2: y(stats.acpl) }),
    svgEl("path", {
      class: "line",
      d: games.map((g, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(g.acpl).toFixed(1)}`).join(""),
    }),
  ];
  games.forEach((g, i) => {
    const link = svgEl("a", { href: `#/a/${encodeURIComponent(g.id)}` });
    const point = svgEl("circle", { class: `point ${g.result || ""}`, cx: x(i), cy: y(g.acpl), r: 5 });
    const title = svgEl("title");
    title.textContent = `${g.date || "?"} vs ${g.opponent || "?"} (${g.result || "?"}): ${g.acpl}`;
    point.append(title);
    link.append(point);
    nodes.push(link);
  });
  svg.replaceChildren(...nodes);

  document.getElementById("trend-caption").textContent =
    "Average centipawn loss per game, oldest first; lower is better. The dashed line is your average. Tap a point to open the game.";
}

function renderProgressDetails(stats) {
  const box = document.getElementById("progress-details");
  const children = [];
  const phases = Object.entries(stats.phases);
  if (phases.length) {
    children.push(
      el("h3", { text: "Centipawn loss by phase" }),
      el(
        "div",
        { class: "phase-stats" },
        ...phases.map(([phase, data]) =>
          el(
            "div",
            {},
            el("div", { class: "stat", text: String(data.acpl) }),
            el("div", { class: "stat-label", text: `${PHASE_LABELS[phase]} · ${data.moves} moves` }),
          ),
        ),
      ),
    );
  }
  const categories = Object.entries(stats.categories);
  if (categories.length) {
    const most = Math.max(...categories.map(([, n]) => n));
    children.push(
      el("h3", { text: "Kinds of mistakes and blunders" }),
      el(
        "ul",
        { class: "alts" },
        ...categories.map(([category, n]) =>
          el(
            "li",
            { class: "alt wide" },
            el("span", { text: CATEGORY_INFO[category]?.label ?? category }),
            el("div", { class: "bar" }, el("div", { style: `width:${Math.round((100 * n) / most)}%` })),
            el("span", { class: "muted small", text: String(n) }),
          ),
        ),
      ),
    );
  }
  if (stats.openings.length) {
    children.push(
      el("h3", { text: "Openings" }),
      el("p", { class: "muted small", text: "Left book: the move where you left opening theory, on average." }),
      el(
        "table",
        { class: "openings" },
        el(
          "thead",
          {},
          el("tr", {}, ...["Opening", "Games", "Left book", "Score"].map((h) => el("th", { text: h }))),
        ),
        el(
          "tbody",
          {},
          ...stats.openings.map((o) =>
            el(
              "tr",
              {},
              el("td", {}, el("span", { class: `piece-dot ${o.color}` }), o.name),
              el("td", { text: String(o.games) }),
              el("td", {
                text: o.left_book === null ? "–" : `move ${o.left_book}`,
                class: o.advice ? `advice-${o.advice}` : null,
              }),
              el("td", { text: o.score === null ? "–" : `${Math.round(o.score * 100)}%` }),
            ),
          ),
        ),
      ),
    );
  }
  const time = stats.time;
  if (time) {
    const parts = [`${formatSeconds(time.average)} per move on average`];
    if (time.time_trouble) parts.push(`${plural(time.time_trouble, "mistake")} in time trouble`);
    if (time.impulsive) parts.push(`${plural(time.impulsive, "mistake")} played in under 3 seconds`);
    if (time.wasted_time) parts.push(`${plural(time.wasted_time, "long think")} on an obvious move`);
    children.push(el("h3", { text: "Clock" }), el("p", { class: "small", text: `${parts.join(", ")}.` }));
  }
  box.replaceChildren(...children);
}

function gameRow(game) {
  const letter = { win: "W", loss: "L", draw: "D" }[game.result];
  const meta = [game.time_class, formatTimeControl(game.time_control), timeAgo(game.end_time)]
    .filter(Boolean)
    .join(" · ");
  const button = el(
    "button",
    { type: "button", class: "game", "aria-label": `${game.result} against ${game.opponent.username}` },
    el("span", { class: `result ${game.result}`, text: letter }),
    el(
      "span",
      {},
      el(
        "div",
        { class: "game-opponent" },
        `vs ${game.opponent.username} `,
        el("span", { class: "muted", text: `(${game.opponent.rating ?? "?"})` }),
      ),
      el("div", { class: "game-meta", text: meta }),
    ),
    el(
      "span",
      { class: "game-side" },
      el("span", { class: `piece-dot ${game.color}` }),
      String(game.rating ?? ""),
    ),
  );
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      const job = await api("/api/analyses", {
        method: "POST",
        body: JSON.stringify({ pgn: game.pgn }),
      });
      location.hash = `#/a/${job.id}`;
    } catch (err) {
      button.disabled = false;
      document.getElementById("games-status").textContent = err.message;
    }
  });
  return el("li", {}, button);
}

// ---------------------------------------------------------------- analysis

async function renderAnalysis(id) {
  const token = routeToken;
  view.replaceChildren(document.getElementById("analysis-template").content.cloneNode(true));
  const pending = document.getElementById("pending");
  const failed = document.getElementById("failed");

  while (token === routeToken) {
    let job;
    try {
      job = await api(`/api/analyses/${encodeURIComponent(id)}`);
    } catch (err) {
      if (token !== routeToken) return;
      failed.hidden = false;
      pending.hidden = true;
      document.getElementById("failed-error").textContent = err.message;
      return;
    }
    if (token !== routeToken) return;

    if (job.status === "done") {
      pending.hidden = true;
      rememberAnalysis(id, job.result);
      new AnalysisView(job.result, id, token).mount();
      return;
    }
    if (job.status === "failed") {
      pending.hidden = true;
      failed.hidden = false;
      document.getElementById("failed-error").textContent = job.error || "Unknown error.";
      return;
    }

    pending.hidden = false;
    const status = document.getElementById("pending-status");
    const fill = document.getElementById("progress-fill");
    const percent = job.total ? Math.round((100 * job.done) / job.total) : 0;
    if (job.status === "queued") {
      status.textContent = job.queue_position
        ? `Waiting in line: ${job.queue_position} game${job.queue_position === 1 ? "" : "s"} ahead.`
        : "Starting…";
    } else {
      status.textContent = `Analysed ${job.done} of ${job.total} positions.`;
    }
    fill.style.width = `${percent}%`;
    fill.parentElement.setAttribute("aria-valuenow", String(percent));
    await new Promise((resolve) => setTimeout(resolve, 1500));
  }
}

class AnalysisView {
  constructor(result, id, token) {
    this.result = result;
    this.id = id;
    this.token = token;
    this.moves = result.moves;
    this.ply = 0;
    this.showBest = false;
    const h = result.headers;
    const me = store.get("username", "");
    this.me = sameName(h.White, me) ? "white" : sameName(h.Black, me) ? "black" : null;
    this.flipped = this.me === "black";
  }

  mount() {
    document.getElementById("analysis").hidden = false;
    this.board = document.getElementById("board");
    this.renderPlayers();
    this.renderMoveList();
    this.renderSummary();
    this.renderOpening();
    this.renderMeta();
    this.bindControls();
    this.go(0);
  }

  get current() {
    return this.ply > 0 ? this.moves[this.ply - 1] : null;
  }

  fenAt(ply) {
    return ply > 0 ? this.moves[ply - 1].fen : this.result.start_fen;
  }

  // Evaluations are null for opening-book positions, which are not analysed.
  evalAt(ply) {
    return ply > 0 ? this.moves[ply - 1].eval_after : (this.moves[0]?.eval_before ?? null);
  }

  sourceAt(ply) {
    return ply > 0 ? this.moves[ply - 1].source : null;
  }

  go(ply) {
    this.ply = Math.max(0, Math.min(this.moves.length, ply));
    this.showBest = false;
    this.update();
  }

  update() {
    this.renderBoard();
    this.renderEvalBar();
    this.renderMoveInfo();
    this.renderGraph();
    this.highlightMove();
  }

  side(color) {
    const h = this.result.headers;
    return color === "white"
      ? { name: h.White || "White", elo: h.WhiteElo }
      : { name: h.Black || "Black", elo: h.BlackElo };
  }

  sideLabel(color) {
    if (this.me) return color === this.me ? "You" : "Your opponent";
    return color === "white" ? "White" : "Black";
  }

  renderPlayers() {
    const top = this.flipped ? "white" : "black";
    const bottom = this.flipped ? "black" : "white";
    for (const [id, color] of [["player-top", top], ["player-bottom", bottom]]) {
      const { name, elo } = this.side(color);
      document.getElementById(id).replaceChildren(
        el(
          "span",
          {},
          el("span", { class: `piece-dot ${color}` }),
          name,
          this.me === color ? el("span", { class: "you", text: "you" }) : null,
        ),
        el("span", { class: "muted", text: elo && elo !== "?" ? elo : "" }),
      );
    }
    document.getElementById("evalbar").classList.toggle("flipped", this.flipped);
  }

  renderBoard() {
    const showBest = this.showBest && this.current?.best_uci;
    const fen = showBest ? this.current.fen_before : this.fenAt(this.ply);
    const rows = fen.split(" ")[0].split("/");
    const grid = rows.map((row) => {
      const out = [];
      for (const ch of row) {
        if (/\d/.test(ch)) out.push(...Array(Number(ch)).fill(null));
        else out.push(ch);
      }
      return out;
    });

    const move = this.current;
    const highlight = new Set();
    let badgeSquare = null;
    if (move && !showBest) {
      highlight.add(move.uci.slice(0, 2)).add(move.uci.slice(2, 4));
      badgeSquare = move.uci.slice(2, 4);
    }

    const squares = [];
    for (let r = 0; r < 8; r++) {
      for (let f = 0; f < 8; f++) {
        const rank = this.flipped ? r : 7 - r; // 0-based rank of this cell
        const file = this.flipped ? 7 - f : f;
        const name = "abcdefgh"[file] + (rank + 1);
        const piece = grid[7 - rank][file];
        const sq = el("div", {
          class: `sq ${(rank + file) % 2 ? "light" : "dark"}${highlight.has(name) ? " hl" : ""}`,
        });
        if (piece) {
          const code = (piece === piece.toUpperCase() ? "w" : "b") + piece.toUpperCase();
          sq.append(el("img", { src: `/api/pieces/${code}.svg`, alt: "", draggable: "false" }));
        }
        if (f === 0) sq.append(el("span", { class: "coord rank", text: String(rank + 1) }));
        if (r === 7) sq.append(el("span", { class: "coord file", text: "abcdefgh"[file] }));
        if (name === badgeSquare && CLASS_INFO[move.classification].sym) {
          sq.append(
            el("span", {
              class: `badge cls-${move.classification}`,
              text: CLASS_INFO[move.classification].sym,
              title: CLASS_INFO[move.classification].label,
            }),
          );
        }
        squares.push(sq);
      }
    }
    if (showBest) squares.push(this.arrow(this.current.best_uci));
    this.board.replaceChildren(...squares);
    this.board.setAttribute(
      "aria-label",
      move ? `Position after ${move.label}` : "Starting position",
    );
  }

  arrow(uci) {
    const center = (sq) => {
      const file = "abcdefgh".indexOf(sq[0]);
      const rank = Number(sq[1]) - 1;
      const x = this.flipped ? 7 - file : file;
      const y = this.flipped ? rank : 7 - rank;
      return [x + 0.5, y + 0.5];
    };
    const [x1, y1] = center(uci.slice(0, 2));
    const [x2, y2] = center(uci.slice(2, 4));
    const len = Math.hypot(x2 - x1, y2 - y1);
    const [ux, uy] = [(x2 - x1) / len, (y2 - y1) / len];
    const svg = svgEl("svg", { class: "arrows", viewBox: "0 0 8 8" });
    const head = 0.42;
    const [bx, by] = [x2 - ux * head, y2 - uy * head];
    svg.append(
      svgEl("line", {
        x1, y1, x2: bx, y2: by,
        stroke: "var(--arrow)", "stroke-width": 0.17, "stroke-linecap": "round", opacity: 0.85,
      }),
      svgEl("polygon", {
        points: [
          [x2, y2],
          [bx - uy * 0.24, by + ux * 0.24],
          [bx + uy * 0.24, by - ux * 0.24],
        ].map((p) => p.join(",")).join(" "),
        fill: "var(--arrow)", opacity: 0.85,
      }),
    );
    return svg;
  }

  renderEvalBar() {
    const white = 50 + 50 * winChance(this.evalAt(this.ply));
    document.getElementById("evalbar-white").style.height = `${white}%`;
  }

  renderMoveInfo() {
    const box = document.getElementById("move-info");
    const move = this.current;
    if (!move) {
      box.replaceChildren(
        el("div", { class: "headline" }, el("span", { class: "move", text: "Start" })),
        el("p", { class: "muted", text: "Use the arrows, swipe the board or tap a move." }),
      );
      return;
    }
    const info = CLASS_INFO[move.classification];
    const children = [
      el(
        "div",
        { class: "headline" },
        el("span", { class: "move", text: `${move.label}${info.sym && info.sym !== "★" ? info.sym : ""}` }),
        el("span", { class: `pill cls-${move.classification}`, text: info.label }),
        move.eval_after === null
          ? null
          : el("span", { class: "eval", text: formatEval(move.eval_after, move.source) }),
      ),
    ];
    if (move.classification === "book") {
      children.push(el("p", { class: "muted", text: "Opening theory, so the engine skipped it." }));
    } else if (move.classification === "forced") {
      children.push(el("p", { class: "muted", text: "The only legal move." }));
    } else if (move.classification === "best") {
      children.push(el("p", { class: "muted", text: "The top choice." }));
    } else if (move.best_san) {
      // Losses are capped at 10 pawns per side, so huge swings read better as evals.
      const lost =
        move.cp_loss && move.cp_loss < 1000
          ? ` This cost ${(move.cp_loss / 100).toFixed(1)} pawns.`
          : "";
      children.push(
        el("p", {}, "Best was ", el("strong", { text: move.best_san }), ` (${formatEval(move.eval_before, move.source_before)}).${lost}`),
      );
      const toggle = el("button", {
        type: "button",
        text: this.showBest ? "Show the game move" : "Show best move",
        onclick: () => {
          this.showBest = !this.showBest;
          this.renderBoard();
          this.renderMoveInfo();
        },
      });
      children.push(el("div", { class: "actions" }, toggle));
    }
    const category = CATEGORY_INFO[move.category];
    if (category) {
      const replier = this.sideLabel(move.color === "white" ? "black" : "white");
      children.splice(
        2,
        0,
        el("p", {}, el("strong", { text: `${category.label}. ` }), category.text(move, replier)),
      );
    }
    if (move.time_spent !== null && move.time_spent !== undefined) {
      const clock = `Took ${formatSeconds(move.time_spent)}, ${formatSeconds(move.clock)} left.`;
      children.push(
        el(
          "p",
          { class: "muted small" },
          clock,
          move.time_flag ? el("strong", { class: "time-flag", text: ` ${TIME_FLAG_INFO[move.time_flag]}` }) : null,
        ),
      );
    }
    if (move.source === "tablebase") {
      children.push(el("p", { class: "muted small", text: "Exact result from the endgame tablebase." }));
    }
    box.replaceChildren(...children);
  }

  renderGraph() {
    const svg = document.getElementById("graph");
    const width = Math.max(200, svg.clientWidth || 320);
    const height = 120;
    const pad = 6;
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    const n = this.moves.length;
    const x = (ply) => pad + ((width - 2 * pad) * ply) / Math.max(1, n);
    const y = (cp) => height / 2 - (height / 2 - pad) * winChance(cp);
    // Book positions have no evaluation: the line has gaps there.
    const segments = [];
    let current = null;
    for (let ply = 0; ply <= n; ply++) {
      const cp = this.evalAt(ply);
      if (cp === null || cp === undefined) {
        current = null;
        continue;
      }
      if (!current) segments.push((current = []));
      current.push([x(ply), y(cp)]);
    }
    const toPath = (pts) => pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("");
    const line = segments.map(toPath).join("");
    const area = segments
      .map((pts) => `${toPath(pts)}L${pts.at(-1)[0]},${height / 2}L${pts[0][0]},${height / 2}Z`)
      .join("");

    const nodes = [svgEl("line", { class: "zero", x1: 0, x2: width, y1: height / 2, y2: height / 2 })];
    let bookEnd = 0;
    while (bookEnd < n && this.moves[bookEnd].classification === "book") bookEnd++;
    if (bookEnd > 0) {
      nodes.push(svgEl("rect", { class: "book-band", x: x(0), y: 0, width: x(bookEnd) - x(0), height }));
      const label = svgEl("text", { class: "book-label", x: x(0) + 4, y: 14 });
      label.textContent = "Book";
      nodes.push(label);
    }
    nodes.push(
      svgEl("path", { class: "area", d: area }),
      svgEl("path", { class: "line", d: line }),
      svgEl("line", { class: "cursor", x1: x(this.ply), x2: x(this.ply), y1: 0, y2: height }),
    );
    for (const move of this.moves) {
      if (["inaccuracy", "mistake", "blunder"].includes(move.classification)) {
        const marker = svgEl("circle", {
          class: `marker ${move.classification}`,
          cx: x(move.ply),
          cy: y(move.eval_after),
          r: move.classification === "inaccuracy" ? 4 : 5,
        });
        const title = svgEl("title");
        title.textContent = `${move.label} ${CLASS_INFO[move.classification].label}`;
        marker.append(title);
        nodes.push(marker);
      }
    }
    svg.replaceChildren(...nodes);
    svg.setAttribute(
      "aria-label",
      `Evaluation graph. Current position ${formatEval(this.evalAt(this.ply), this.sourceAt(this.ply))}.`,
    );
  }

  renderMoveList() {
    const list = document.getElementById("moves");
    const items = [];
    for (let i = 0; i < this.moves.length; i += 2) {
      const white = this.moves[i];
      const black = this.moves[i + 1];
      items.push(
        el(
          "li",
          {},
          el("span", { class: "num", text: `${(i + 2) / 2}.` }),
          this.moveButton(white),
          black ? this.moveButton(black) : el("span"),
        ),
      );
    }
    list.replaceChildren(...items);
  }

  moveButton(move) {
    const info = CLASS_INFO[move.classification];
    const sym = info.sym && info.sym !== "★" ? info.sym : "";
    return el(
      "button",
      {
        type: "button",
        class: `cls-${move.classification}`,
        "data-ply": move.ply,
        "aria-label": `${move.label}, ${info.label}`,
        onclick: () => this.go(move.ply),
      },
      move.san,
      sym ? el("span", { class: "sym", text: sym }) : null,
    );
  }

  highlightMove() {
    const list = document.getElementById("moves");
    for (const b of list.querySelectorAll("button.current")) b.classList.remove("current");
    const button = list.querySelector(`button[data-ply="${this.ply}"]`);
    if (button) {
      button.classList.add("current");
      const top = button.offsetTop - list.offsetTop;
      if (top < list.scrollTop || top > list.scrollTop + list.clientHeight - 40) {
        list.scrollTop = top - list.clientHeight / 2;
      }
    }
  }

  renderSummary() {
    const order = this.me === "black" ? ["black", "white"] : ["white", "black"];
    const box = document.getElementById("summary");
    box.replaceChildren(
      ...order.map((color) => {
        const s = this.result.summary[color];
        const { name } = this.side(color);
        const worst = s.worst.map((ply) => this.moves[ply - 1]);
        return el(
          "div",
          { class: "side" },
          el("h3", {}, el("span", { class: `piece-dot ${color}` }), name, this.me === color ? el("span", { class: "muted", text: "(you)" }) : null),
          el("div", { class: "stat", text: String(s.acpl) }),
          el("div", { class: "stat-label", text: "average centipawn loss" }),
          el(
            "ul",
            { class: "counts" },
            ...Object.keys(CLASS_INFO).map((cls) =>
              el(
                "li",
                { class: `cls-${cls}` },
                el("span", { text: CLASS_INFO[cls].label }),
                el("span", { text: String(s.counts[cls] ?? 0) }),
              ),
            ),
          ),
          worst.length
            ? el(
                "ul",
                { class: "jump-list" },
                ...worst.map((m) =>
                  el(
                    "li",
                    {},
                    el(
                      "button",
                      { type: "button", class: `cls-${m.classification}`, onclick: () => this.go(m.ply) },
                      el("span", { text: `${m.label}${CLASS_INFO[m.classification].sym}` }),
                      el("span", { class: "muted", text: `best ${m.best_san}` }),
                    ),
                  ),
                ),
              )
            : el("p", { class: "muted small", text: "No mistakes or blunders." }),
          this.categorySummary(s.categories),
          this.timeSummary(s.time),
        );
      }),
    );
  }

  categorySummary(categories) {
    const entries = Object.entries(categories || {});
    if (!entries.length) return null;
    return el(
      "p",
      { class: "small" },
      entries.map(([c, n]) => `${CATEGORY_INFO[c]?.label ?? c}${n > 1 ? ` ×${n}` : ""}`).join(", "),
    );
  }

  timeSummary(time) {
    if (!time) return null;
    const parts = [`${formatSeconds(time.average)} per move`];
    if (time.time_trouble) parts.push(`${plural(time.time_trouble, "mistake")} in time trouble`);
    if (time.impulsive) parts.push(`${plural(time.impulsive, "impulsive mistake")}`);
    if (time.wasted_time) parts.push(`${plural(time.wasted_time, "long think")} on an obvious move`);
    return el("p", { class: "muted small", text: `Clock: ${parts.join(", ")}.` });
  }

  renderOpening() {
    const box = document.getElementById("opening");
    const opening = this.result.opening;
    const children = [];
    if (opening.name) children.push(el("p", {}, el("strong", { text: opening.name })));
    const dev = opening.deviation;
    if (!opening.checked) {
      children.push(
        el("p", { class: "muted", text: opening.error || "Opening theory was not checked." }),
      );
    } else if (!dev) {
      children.push(el("p", { text: "Both sides stayed in book for the whole opening." }));
    } else {
      const who = this.sideLabel(dev.color);
      children.push(
        el(
          "p",
          {},
          `${who} left theory with `,
          el("button", { type: "button", text: dev.label, onclick: () => this.go(dev.ply) }),
          ".",
        ),
      );
      if (dev.alternatives.length) {
        children.push(el("p", { class: "muted small", text: "Book moves in that position, best first:" }));
        children.push(
          el(
            "ul",
            { class: "alts" },
            ...dev.alternatives.map((alt) => {
              const score = alt.score;
              return el(
                "li",
                { class: "alt" },
                el("span", { class: "san", text: alt.san }),
                score === null
                  ? el("span", { class: "muted", text: `weight ${alt.games}` })
                  : el("div", { class: "bar", title: `${Math.round(score * 100)}%` }, el("div", { style: `width:${Math.round(score * 100)}%` })),
                el(
                  "span",
                  { class: "muted small" },
                  score === null ? "" : `${Math.round(score * 100)}% · ${alt.games.toLocaleString()} games`,
                ),
              );
            }),
          ),
        );
      } else {
        children.push(el("p", { class: "muted", text: "The position was not in the book at all." }));
      }
    }
    box.replaceChildren(...children);
  }

  renderMeta() {
    const h = this.result.headers;
    const meta = document.getElementById("game-meta");
    const parts = [h.Date, h.Result, h.Termination].filter((p) => p && !p.includes("?"));
    meta.replaceChildren(parts.join(" · "));
    const engine = `${this.result.engine_name || "Stockfish"}, ${this.result.engine}`;
    meta.append(el("br"), engine);
    const positions = this.result.positions;
    if (positions) {
      const total = Object.values(positions).reduce((a, b) => a + b, 0);
      const skipped = ["book", "tablebase", "forced"]
        .filter((k) => positions[k])
        .map((k) => `${positions[k]} ${k}`);
      meta.append(
        el("br"),
        `Engine searched ${positions.engine ?? 0} of ${total} positions` +
          (skipped.length ? ` (${skipped.join(", ")})` : ""),
      );
    }
    if (h.Link && /^https:\/\//.test(h.Link)) {
      meta.append(el("br"), el("a", { href: h.Link, target: "_blank", rel: "noopener", text: "View on Chess.com" }));
    }
  }

  bindControls() {
    const actions = {
      start: () => this.go(0),
      prev: () => this.go(this.ply - 1),
      next: () => this.go(this.ply + 1),
      end: () => this.go(this.moves.length),
    };
    for (const button of document.querySelectorAll("[data-nav]")) {
      button.addEventListener("click", actions[button.dataset.nav]);
    }
    document.getElementById("flip").addEventListener("click", () => {
      this.flipped = !this.flipped;
      this.renderPlayers();
      this.renderBoard();
    });
    document.getElementById("share").addEventListener("click", () => this.share());

    const onKey = (event) => {
      if (this.token !== routeToken) {
        document.removeEventListener("keydown", onKey);
        return;
      }
      if (event.target.closest("input, textarea, select")) return;
      const key = { ArrowLeft: "prev", ArrowRight: "next", Home: "start", End: "end" }[event.key];
      if (key) {
        event.preventDefault();
        actions[key]();
      }
    };
    document.addEventListener("keydown", onKey);

    // Swipe the board to step through moves.
    let startX = null;
    this.board.addEventListener("pointerdown", (e) => (startX = e.clientX));
    this.board.addEventListener("pointerup", (e) => {
      if (startX === null) return;
      const dx = e.clientX - startX;
      startX = null;
      if (Math.abs(dx) > 30) (dx < 0 ? actions.next : actions.prev)();
    });

    // Tap or drag on the graph to scrub through the game.
    const graph = document.getElementById("graph");
    const scrub = (e) => {
      const rect = graph.getBoundingClientRect();
      const frac = (e.clientX - rect.left) / rect.width;
      const ply = Math.round(frac * this.moves.length);
      if (ply !== this.ply) this.go(ply);
    };
    graph.addEventListener("pointerdown", (e) => {
      graph.setPointerCapture(e.pointerId);
      scrub(e);
    });
    graph.addEventListener("pointermove", (e) => {
      if (graph.hasPointerCapture(e.pointerId)) scrub(e);
    });

    const onResize = () => {
      if (this.token !== routeToken) window.removeEventListener("resize", onResize);
      else this.renderGraph();
    };
    window.addEventListener("resize", onResize);
  }

  async share() {
    const url = `${location.origin}/#/a/${this.id}`;
    const h = this.result.headers;
    const title = `${h.White} vs ${h.Black}`;
    try {
      if (navigator.share) {
        await navigator.share({ title, url });
        return;
      }
      await navigator.clipboard.writeText(url);
      const button = document.getElementById("share");
      button.textContent = "Copied";
      setTimeout(() => (button.textContent = "Share"), 1500);
    } catch {
      /* the user cancelled the share sheet */
    }
  }
}

// ---------------------------------------------------------------- router

function route() {
  routeToken += 1;
  const hash = location.hash.replace(/^#/, "");
  const analysis = hash.match(/^\/a\/([\w-]+)$/);
  const user = hash.match(/^\/u\/([^/]+)$/);
  window.scrollTo(0, 0);
  if (analysis) renderAnalysis(analysis[1]);
  else renderHome(user ? decodeURIComponent(user[1]) : null);
}

window.addEventListener("hashchange", route);
route();
