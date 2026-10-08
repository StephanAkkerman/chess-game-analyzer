// Puzzle trainer: positions from the user's own games where they missed the
// best move, solved by moving the pieces and brought back on a spaced-
// repetition schedule (see chess_analyzer/puzzles.py).
//
// The server sends everything a puzzle needs, including the legal moves of
// every position the solver has to move in, so no chess rules live here.
// A move other than the engine's is checked by Stockfish in this browser,
// when it can run, and counts if it is about as good.

import { BrowserEngine, deviceSupported } from "./device.js";

let ctx = null;

// `deps` are helpers from app.js: api, el, svgEl, store, getConfig,
// formatEval, routeToken() and the CLASS_INFO and CATEGORY_INFO tables.
export function initPuzzles(deps) {
  ctx = deps;
  document.head.append(Object.assign(document.createElement("link"), { rel: "stylesheet", href: "/static/puzzles.css" }));
}

const OPTIONS_KEY = "puzzleOptions";
const MATE = 10000;
const EVAL_CAP = 1000;
// Another move counts when it keeps the position within this many
// centipawns of the engine's move (both capped at EVAL_CAP).
const TOLERANCE = 60;
const CHECK_LIMIT = { movetime: 1500 };
const REPLY_DELAY = 500;
const STEP_DELAY = 900;
const KIND_LABELS = {
  critical: "Missed critical moment",
  mistake: "Mistake",
  miss: "Miss",
  blunder: "Blunder",
};

function options() {
  return { visualize: false, leadIn: false, ...ctx.store.get(OPTIONS_KEY, {}) };
}

function setOption(name, value) {
  ctx.store.set(OPTIONS_KEY, { ...options(), [name]: value });
}

// The user's own date, so "tomorrow" follows their day.
function localDate() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function sideName(color) {
  return color === "white" ? "White" : "Black";
}

function cap(cp) {
  return Math.max(-EVAL_CAP, Math.min(EVAL_CAP, cp));
}

// Moves in book notation from `fen` on: "5.Nxe5 Bxd1 6.Bxf7+".
function lineText(fen, sans) {
  const parts = fen.split(" ");
  let white = parts[1] === "w";
  let number = Number(parts[5]) || 1;
  return sans
    .map((san, i) => {
      let text;
      if (white) text = `${number}.${san}`;
      else {
        text = i === 0 ? `${number}...${san}` : san;
        number += 1;
      }
      white = !white;
      return text;
    })
    .join(" ");
}

function inDays(due, today) {
  const days = Math.round((Date.parse(due) - Date.parse(today)) / 86400000);
  if (days <= 0) return "today";
  if (days === 1) return "tomorrow";
  return `in ${days} days`;
}

// The piece on each square of a FEN position, by square name.
function pieces(fen) {
  const board = {};
  fen.split(" ")[0].split("/").forEach((row, i) => {
    let file = 0;
    for (const ch of row) {
      if (/\d/.test(ch)) file += Number(ch);
      else {
        board["abcdefgh"[file] + (8 - i)] = ch;
        file += 1;
      }
    }
  });
  return board;
}

// ---------------------------------------------------------------- engine

// Stockfish for checking moves other than the engine's: separate from the
// one that analyses games, which may be busy.
let checker = null;

// Score of `uci` played in `fen`, from the mover's point of view, or null
// if this browser cannot tell.
async function scoreMove(fen, uci) {
  const config = await ctx.getConfig();
  if (!config.device_engine || !deviceSupported()) return null;
  checker ??= new BrowserEngine(config.device_engine);
  try {
    const result = await checker.search({ fen, moves: [uci], searchmoves: [] }, CHECK_LIMIT);
    // The score is the opponent's: they are to move.
    if (result.mate !== undefined) return result.mate > 0 ? -MATE : MATE;
    return -result.cp;
  } catch {
    if (checker.failure || !checker.worker) checker = null;
    return null;
  }
}

// ---------------------------------------------------------------- board

function drawBoard(board, fen, { flipped, lastMove, selected, targets, wrong }) {
  const { el } = ctx;
  const position = pieces(fen);
  const highlight = new Set(lastMove ? [lastMove.slice(0, 2), lastMove.slice(2, 4)] : []);
  const squares = [];
  for (let r = 0; r < 8; r++) {
    for (let f = 0; f < 8; f++) {
      const rank = flipped ? r : 7 - r;
      const file = flipped ? 7 - f : f;
      const name = "abcdefgh"[file] + (rank + 1);
      const classes = ["sq", (rank + file) % 2 ? "light" : "dark"];
      if (highlight.has(name)) classes.push("hl");
      if (name === selected) classes.push("selected");
      if (wrong?.includes(name)) classes.push("wrong");
      const sq = el("div", { class: classes.join(" "), "data-sq": name });
      const piece = position[name];
      if (piece) {
        const code = (piece === piece.toUpperCase() ? "w" : "b") + piece.toUpperCase();
        sq.append(el("img", { src: `/api/pieces/${code}.svg`, alt: "", draggable: "false" }));
      }
      if (targets?.has(name)) sq.append(el("span", { class: piece ? "target capture" : "target" }));
      if (f === 0) sq.append(el("span", { class: "coord rank", text: String(rank + 1) }));
      if (r === 7) sq.append(el("span", { class: "coord file", text: "abcdefgh"[file] }));
      squares.push(sq);
    }
  }
  board.replaceChildren(...squares);
}

// ---------------------------------------------------------------- trainer

// One puzzle after the other. With a username, the attempts are recorded
// and schedule the puzzles' next reviews; without, it is practice only.
class PuzzleSession {
  constructor(puzzles, { username = null, day = null, back = null } = {}) {
    this.queue = puzzles.map((puzzle) => ({ puzzle, again: false }));
    this.username = username;
    this.day = day;
    this.back = back;
    this.index = 0;
    this.token = 0;
    this.results = [];
    this.routeToken = ctx.routeToken();
  }

  get alive() {
    return this.routeToken === ctx.routeToken();
  }

  mount(view) {
    const { el } = ctx;
    this.board = el("div", { class: "board puzzle-board", role: "img", "aria-label": "Puzzle position" });
    this.info = el("section", { class: "card move-info puzzle-info", "aria-live": "polite" });
    this.side = el("section", { class: "card puzzle-side" });
    this.top = el("div", { class: "player" });
    this.bottom = el("div", { class: "player" });
    this.prevButton = el("button", { type: "button", "aria-label": "Back", text: "◀", onclick: () => this.replayTo(this.frame - 1) });
    this.nextButton = el("button", { type: "button", "aria-label": "Forward", text: "▶", onclick: () => this.replayTo(this.frame + 1) });
    view.replaceChildren(
      el(
        "div",
        { class: "analysis puzzle-page" },
        el(
          "div",
          { class: "board-column" },
          this.top,
          el("div", { class: "board-wrap" }, this.board),
          this.bottom,
          el("div", { class: "controls puzzle-controls" }, this.prevButton, this.nextButton),
        ),
        el("div", { class: "side-column" }, this.info, this.side),
      ),
    );
    this.bindBoard();
    document.addEventListener("keydown", (this.onKey = (event) => {
      if (!this.alive) return document.removeEventListener("keydown", this.onKey);
      if (this.state !== "done" || event.target.closest?.("input, textarea, select")) return;
      if (event.key === "ArrowLeft") this.replayTo(this.frame - 1);
      if (event.key === "ArrowRight") this.replayTo(this.frame + 1);
    }));
    this.start();
  }

  // ------------------------------------------------------------ puzzle state

  start() {
    const item = this.queue[this.index];
    this.token += 1;
    this.selected = null;
    this.wrong = null;
    this.message = null;
    this.failed = false;
    this.alternative = null;
    this.review = null;
    if (!item) {
      this.state = "finished";
      this.render();
      return;
    }
    const p = (this.puzzle = item.puzzle);
    this.again = item.again;
    this.options = options();
    this.flipped = p.color === "black";
    this.ply = 0; // moves of the solution played so far
    const leadMoves = this.options.leadIn ? p.lead_in.moves : [];
    // Positions to step through once the puzzle is over: the lead-in, if
    // shown, then the puzzle and its solution.
    this.frames = [
      { fen: leadMoves.length ? p.lead_in.fen : p.fen, uci: null },
      ...leadMoves.map((m) => ({ fen: m.fen, uci: m.uci })),
      ...p.solution.map((m) => ({ fen: m.fen, uci: m.uci })),
    ];
    this.startFrame = leadMoves.length;
    this.leadMoves = leadMoves;
    this.renderPlayers();
    if (leadMoves.length && !this.options.visualize) {
      // Play the game moves that led to the puzzle, to see it coming.
      this.state = "lead";
      this.frame = 0;
      this.render();
      this.animate(this.startFrame, () => this.begin());
    } else {
      this.begin();
    }
  }

  begin() {
    this.state = "solve";
    this.frame = this.startFrame;
    this.render();
  }

  // Step the board from the current frame to `last`, then call `then`.
  animate(last, then) {
    const token = this.token;
    const step = () => {
      if (token !== this.token || !this.alive) return;
      if (this.frame >= last) return then?.();
      this.frame += 1;
      this.renderBoard();
      setTimeout(step, STEP_DELAY);
    };
    setTimeout(step, STEP_DELAY);
  }

  get step() {
    return this.puzzle.steps[this.ply / 2];
  }

  // The position the solver is to move in (not shown when visualising).
  get fen() {
    return this.ply ? this.puzzle.solution[this.ply - 1].fen : this.puzzle.fen;
  }

  get frozen() {
    return ["solve", "check", "reply"].includes(this.state) && this.options.visualize;
  }

  // ------------------------------------------------------------ input

  bindBoard() {
    const squareAt = (event) =>
      document.elementFromPoint(event.clientX, event.clientY)?.closest?.("[data-sq]")?.dataset.sq;
    this.board.addEventListener("pointerdown", (event) => {
      if (this.state !== "solve") return;
      const name = event.target.closest("[data-sq]")?.dataset.sq;
      if (!name) return;
      event.preventDefault();
      this.downOn = name;
      if (this.selected && this.selected !== name && this.tryMove(this.selected, name)) return;
      this.selected = this.selected === name ? null : this.canSelect(name) ? name : null;
      this.renderBoard();
    });
    // Dragging a piece: release it on another square.
    this.board.addEventListener("pointerup", (event) => {
      const from = this.downOn;
      this.downOn = null;
      if (this.state !== "solve" || !this.selected || from !== this.selected) return;
      const to = squareAt(event);
      if (to && to !== from) this.tryMove(from, to);
    });
  }

  canSelect(name) {
    // Visualising, the board does not show where the pieces are now.
    if (this.options.visualize) return true;
    const piece = pieces(this.fen)[name];
    if (!piece) return false;
    return (piece === piece.toUpperCase()) === (this.puzzle.color === "white");
  }

  targets() {
    if (!this.selected || this.options.visualize || this.state !== "solve") return null;
    return new Set(
      Object.keys(this.step.legal)
        .filter((uci) => uci.startsWith(this.selected))
        .map((uci) => uci.slice(2, 4)),
    );
  }

  // Returns whether the squares made a move (right or wrong).
  tryMove(from, to) {
    const legal = Object.keys(this.step.legal).filter((uci) => uci.slice(0, 4) === from + to);
    if (!legal.length) {
      if (!this.options.visualize) return false;
      this.selected = null;
      this.message = { tone: "warn", text: "That move isn't legal in the position you should be picturing. Try again." };
      this.render();
      return true;
    }
    const expected = this.puzzle.solution[this.ply].uci;
    const uci = legal.length === 1 ? legal[0] : legal.includes(expected) ? expected : legal.find((m) => m.endsWith("q"));
    this.selected = null;
    this.play(uci);
    return true;
  }

  async play(uci) {
    const p = this.puzzle;
    const san = this.step.legal[uci];
    const expected = p.solution[this.ply].uci;
    this.message = null;
    this.wrong = null;
    if (uci === expected) {
      this.ply += 1;
      if (this.ply >= p.solution.length) return this.finish("solved");
      this.render();
      const token = this.token;
      this.state = "reply";
      setTimeout(() => {
        if (token !== this.token || !this.alive) return;
        this.ply += 1;
        this.state = "solve";
        this.render();
      }, REPLY_DELAY);
      return;
    }
    if (this.step.mates.includes(uci)) return this.finish("alternative", { san, cp: MATE });

    const token = this.token;
    this.state = "check";
    this.message = { tone: "muted", text: `Checking ${san} with Stockfish…` };
    this.render();
    const cp = await scoreMove(this.fen, uci);
    if (token !== this.token || !this.alive) return;
    const sign = p.color === "white" ? 1 : -1;
    const target = p.eval === null || p.eval === undefined ? null : cap(sign * p.eval);
    if (cp !== null && target !== null && cap(cp) >= target - TOLERANCE) {
      return this.finish("alternative", { san, cp });
    }
    this.failed = true;
    this.state = "solve";
    this.wrong = [uci.slice(0, 2), uci.slice(2, 4)];
    const verdict =
      cp === null
        ? `${san} is not the engine's move.`
        : `${san} is not it: it gives ${ctx.formatEval(sign * cp)}.`;
    this.message = { tone: "bad", text: `${verdict} Try again, or show the solution.` };
    this.render();
    setTimeout(() => {
      if (token !== this.token || !this.wrong) return;
      this.wrong = null;
      this.renderBoard();
    }, 900);
  }

  showSolution() {
    this.failed = true;
    this.finish("shown");
  }

  finish(outcome, alternative = null) {
    this.state = "done";
    this.outcome = outcome;
    this.alternative = alternative;
    this.selected = null;
    const solved = !this.failed;
    if (!this.again) this.results.push({ puzzle: this.puzzle, solved });
    // Failed puzzles come back once at the end of the session.
    if (!solved && this.username && !this.again) this.queue.push({ puzzle: this.puzzle, again: true });
    const visualized = this.options.visualize && outcome !== "shown";
    if (visualized || outcome === "shown") {
      // Show the whole line on the board, from the start.
      this.frame = visualized ? 0 : this.startFrame;
      this.render();
      this.animate(this.frames.length - 1);
    } else {
      this.frame = this.startFrame + this.ply;
      this.render();
    }
    if (this.username && !this.again) this.record(solved);
  }

  async record(solved) {
    const token = this.token;
    try {
      this.review = await ctx.api(
        `/api/players/${encodeURIComponent(this.username)}/puzzles/${this.puzzle.key}/review`,
        { method: "POST", body: JSON.stringify({ solved, today: this.day }) },
      );
    } catch (err) {
      this.review = { error: err.message };
    }
    if (token === this.token && this.alive) this.renderInfo();
  }

  replayTo(frame) {
    if (this.state !== "done") return;
    this.token += 1; // stops an animation
    this.frame = Math.max(0, Math.min(this.frames.length - 1, frame));
    this.renderBoard();
  }

  next() {
    this.index += 1;
    this.start();
  }

  retry() {
    // Practice only: the first attempt already set the schedule.
    this.queue.splice(this.index + 1, 0, { puzzle: this.puzzle, again: true });
    this.next();
  }

  // ------------------------------------------------------------ rendering

  render() {
    this.renderBoard();
    this.renderInfo();
    this.renderSide();
  }

  renderPlayers() {
    const p = this.puzzle;
    const { el } = ctx;
    const label = (color) =>
      el("span", {}, el("span", { class: `piece-dot ${color}` }), (color === "white" ? p.white : p.black) || sideName(color));
    this.top.replaceChildren(label(this.flipped ? "white" : "black"));
    this.bottom.replaceChildren(label(this.flipped ? "black" : "white"));
  }

  renderBoard() {
    if (this.state === "finished") return;
    const frozen = this.frozen;
    let fen;
    let lastMove;
    if (frozen) {
      fen = this.frames[0].fen;
      lastMove = null;
    } else if (this.state === "solve" || this.state === "check" || this.state === "reply") {
      fen = this.fen;
      lastMove = this.ply ? this.puzzle.solution[this.ply - 1].uci : this.leadMoves.at(-1)?.uci;
    } else {
      ({ fen, uci: lastMove } = this.frames[this.frame]);
    }
    drawBoard(this.board, fen, {
      flipped: this.flipped,
      lastMove,
      selected: this.selected,
      targets: this.targets(),
      wrong: frozen ? null : this.wrong,
    });
    this.board.classList.toggle("frozen", frozen);
    const done = this.state === "done";
    this.prevButton.disabled = !done || this.frame <= 0;
    this.nextButton.disabled = !done || this.frame >= this.frames.length - 1;
  }

  renderInfo() {
    const { el } = ctx;
    if (this.state === "finished") return this.info.replaceChildren(...this.summary());
    const p = this.puzzle;
    const side = sideName(p.color);
    const done = this.state === "done";
    const children = [
      el(
        "div",
        { class: "headline" },
        el("span", { class: "move", text: this.again ? "Again" : "Puzzle" }),
        done
          ? el("span", { class: `pill cls-${p.classification}${p.kind === "critical" ? " critical" : ""}`, text: KIND_LABELS[p.kind] || p.kind })
          : el("span", { class: "pill", text: `${side} to move` }),
      ),
    ];
    if (this.state === "lead") {
      children.push(
        el("p", { text: "Watch the moves that led to the position: where is the tension building?" }),
        el("div", { class: "actions row" }, el("button", {
          type: "button",
          text: "Skip",
          onclick: () => {
            this.token += 1; // stops the moves playing
            this.begin();
          },
        })),
      );
    } else if (!done) {
      children.push(...this.prompt());
    } else {
      children.push(...this.outcomeInfo());
    }
    this.info.replaceChildren(...children);
  }

  prompt() {
    const { el } = ctx;
    const p = this.puzzle;
    const played = p.solution.slice(0, this.ply).map((m) => m.san);
    const children = [el("p", {}, `${sideName(p.color)} to move. Find the move you missed in the game.`)];
    if (this.options.visualize) {
      children.push(
        el("p", { class: "small" }, el("strong", { text: "No moving. " }), "The pieces stay where they are: picture every move in your head and tap it on this board."),
      );
      if (this.leadMoves.length) {
        children.push(
          el("p", {}, "First picture the game moves ", el("strong", { text: lineText(this.frames[0].fen, this.leadMoves.map((m) => m.san)) }), "."),
        );
      }
    }
    if (played.length) {
      const opponent = played.length % 2 === 0 ? `${sideName(p.color === "white" ? "black" : "white")} replied ${played.at(-1)}. ` : "";
      children.push(
        el("p", {}, opponent, "So far: ", el("strong", { text: lineText(p.fen, played) }), this.state === "reply" ? "" : ". Your move."),
      );
    }
    if (this.message) children.push(el("p", { class: `message ${this.message.tone}`, text: this.message.text }));
    children.push(
      el(
        "div",
        { class: "actions row" },
        el("button", { type: "button", text: "Show the solution", disabled: this.state === "check", onclick: () => this.showSolution() }),
      ),
    );
    return children;
  }

  outcomeInfo() {
    const { el, formatEval } = ctx;
    const p = this.puzzle;
    const children = [];
    if (this.outcome === "alternative") {
      const how = this.alternative.cp >= MATE ? "mates" : `keeps ${formatEval((p.color === "white" ? 1 : -1) * this.alternative.cp)}`;
      children.push(el("p", { class: "message good" }, el("strong", { text: "Solved! " }), `${this.alternative.san} works too: it ${how}.`));
    } else if (this.failed) {
      children.push(
        el("p", { class: `message ${this.outcome === "shown" ? "bad" : "warn"}` }, el("strong", { text: this.outcome === "shown" ? "Not this time. " : "Solved, after a wrong try. " }), this.username && !this.again ? "It comes back tomorrow, and once more at the end of today's puzzles." : ""),
      );
    } else {
      children.push(el("p", { class: "message good" }, el("strong", { text: "Solved!" })));
    }
    const sans = p.solution.map((m) => m.san);
    children.push(
      el("p", {}, this.outcome === "alternative" ? "The engine's line: " : "Solution: ", el("strong", { text: lineText(p.fen, sans) }), p.eval === null ? "" : ` (${formatEval(p.eval, p.source)})`),
    );
    if (p.kind === "critical" && p.second_san) {
      children.push(el("p", { class: "small", text: `Only this kept the balance: the next best move, ${p.second_san}, gives ${formatEval(p.second_eval)}.` }));
    }
    const category = ctx.CATEGORY_INFO[p.category]?.label;
    const cls = ctx.CLASS_INFO[p.classification]?.label;
    children.push(
      el(
        "p",
        { class: "muted small" },
        `In the game: ${p.played.label}${cls ? ` (${cls}${category ? `, ${category.toLowerCase()}` : ""})` : ""}. `,
        p.id ? el("a", { href: `#/a/${encodeURIComponent(p.id)}/m/${p.ply}`, text: "Open the game" }) : null,
      ),
    );
    if (this.review && !this.review.error) {
      children.push(el("p", { class: "muted small", text: `Next review ${inDays(this.review.due, this.day)}.` }));
    } else if (this.review?.error) {
      children.push(el("p", { class: "error small", text: `Could not save the result: ${this.review.error}` }));
    }
    if (this.frames.length > 1) {
      children.push(el("p", { class: "muted small", text: "Step through the line with ◀ ▶ under the board." }));
    }
    const buttons = [];
    if (this.index < this.queue.length - 1) {
      buttons.push(el("button", { type: "button", class: "primary", text: "Next puzzle", onclick: () => this.next() }));
    } else if (this.username) {
      buttons.push(el("button", { type: "button", class: "primary", text: "Finish", onclick: () => this.next() }));
    }
    buttons.push(el("button", { type: "button", text: "Try again", onclick: () => this.retry() }));
    if (this.back) buttons.push(el("a", { class: "button", href: this.back.href, text: this.back.text }));
    children.push(el("div", { class: "actions row" }, ...buttons));
    return children;
  }

  summary() {
    const { el } = ctx;
    const solved = this.results.filter((r) => r.solved).length;
    return [
      el("div", { class: "headline" }, el("span", { class: "move", text: "Done for today" })),
      el("p", { text: this.results.length ? `You solved ${solved} of ${this.results.length} puzzles at the first try.` : "No puzzles to do." }),
      el("p", { class: "muted small", text: "Missed ones come back tomorrow; solved ones after 3 days, then a week, two weeks and longer." }),
      el("div", { class: "actions row" }, el("a", { class: "button primary", href: this.back?.href || "#/", text: "Back to your games" })),
    ];
  }

  renderSide() {
    const { el } = ctx;
    const opts = options();
    const toggle = (name, label, hint) =>
      el(
        "label",
        { class: "toggle" },
        el("input", {
          type: "checkbox",
          checked: opts[name],
          onchange: (event) => {
            setOption(name, event.target.checked);
            // Apply it right away while the puzzle is untouched.
            const untouched = (this.state === "solve" || this.state === "lead") && this.ply === 0 && !this.failed;
            if (untouched) {
              this.start();
            } else {
              this.renderSide();
            }
          },
        }),
        el("span", {}, el("strong", { text: label }), el("span", { class: "muted small block", text: hint })),
      );
    const children = [];
    if (this.username) {
      const total = this.queue.filter((q) => !q.again).length;
      const position = Math.min(this.index + 1, this.queue.length);
      children.push(
        el("h2", { text: "Today's puzzles" }),
        el("p", { class: "muted small", text: this.state === "finished" ? `${total} done.` : `Puzzle ${position} of ${this.queue.length}${this.queue.length > total ? ` (${this.queue.length - total} again)` : ""}.` }),
        el("div", { class: "progress" }, el("div", { class: "progress-fill", style: `width:${Math.round((100 * Math.min(this.index, this.queue.length)) / Math.max(1, this.queue.length))}%` })),
      );
    } else {
      children.push(el("h2", { text: "Options" }));
    }
    children.push(
      el(
        "div",
        { class: "stack options" },
        toggle("visualize", "No moving", "Solve it in your head: the pieces stay put while you enter the whole line."),
        toggle("leadIn", "Start two moves earlier", "See the game moves that led to the position first."),
      ),
    );
    if (this.state === "finished") children.splice(-1, 1);
    this.side.replaceChildren(...children);
  }
}

// ---------------------------------------------------------------- pages

// The player's page, on the site of the current page (#/u/NAME or #/lichess/NAME).
function userHash(username) {
  const site = location.hash.startsWith("#/lichess/") ? "lichess" : "u";
  return `#/${site}/${encodeURIComponent(username)}`;
}

// Today's puzzles for `username`: those due for review, then new ones.
export async function renderPuzzleTrainer(view, username) {
  const { el, api } = ctx;
  const token = ctx.routeToken();
  const day = localDate();
  const back = { href: userHash(username), text: "Back to your games" };
  view.replaceChildren(el("section", { class: "card" }, el("h1", { text: "Puzzles" }), el("p", { class: "muted", text: "Loading today's puzzles…" })));
  let data;
  try {
    data = await api(`/api/players/${encodeURIComponent(username)}/puzzles?today=${day}`);
  } catch (err) {
    if (token !== ctx.routeToken()) return;
    view.replaceChildren(el("section", { class: "card" }, el("h1", { text: "Puzzles" }), el("p", { class: "error", text: err.message }), el("a", { class: "button", href: back.href, text: "Back" })));
    return;
  }
  if (token !== ctx.routeToken()) return;
  if (!data.puzzles.length) {
    const text = data.total
      ? `Nothing left for today: ${data.learned} of ${data.total} puzzles learned. ${data.tomorrow ? `${data.tomorrow} come back tomorrow.` : ""}`
      : "No puzzles yet. Analyse a few games: every position where you missed the best move becomes one.";
    view.replaceChildren(el("section", { class: "card" }, el("h1", { text: "Puzzles" }), el("p", { text }), el("a", { class: "button", href: back.href, text: back.text })));
    return;
  }
  new PuzzleSession(data.puzzles, { username, day: data.today, back }).mount(view);
}

// The puzzle before move `ply` of an analysis, for practice.
export async function renderSinglePuzzle(view, id, ply) {
  const { el, api } = ctx;
  const token = ctx.routeToken();
  const back = { href: `#/a/${encodeURIComponent(id)}/m/${ply}`, text: "Back to the game" };
  let puzzle;
  try {
    puzzle = await api(`/api/analyses/${encodeURIComponent(id)}/puzzles/${ply}`);
  } catch (err) {
    if (token !== ctx.routeToken()) return;
    view.replaceChildren(el("section", { class: "card" }, el("h1", { text: "Puzzle" }), el("p", { class: "error", text: err.message }), el("a", { class: "button", href: `#/a/${encodeURIComponent(id)}`, text: "Back to the game" })));
    return;
  }
  if (token !== ctx.routeToken()) return;
  new PuzzleSession([puzzle], { back }).mount(view);
}

// The trainer's entry on the home page: how many puzzles wait today.
export function puzzleTrainerCard(username, critical) {
  const { el, api } = ctx;
  const status = el("p", { class: "muted small", text: "Loading…" });
  const box = el("div", { class: "trainer-card" }, el("h3", { text: "Puzzles from your games" }), status);
  api(`/api/players/${encodeURIComponent(username)}/puzzles?summary=true&today=${localDate()}`)
    .then((data) => {
      const lines = [];
      if (!data.total) {
        lines.push("Every position where you missed the best move becomes a puzzle you solve by moving the pieces.");
      } else {
        const todo = data.due + data.new;
        lines.push(
          todo
            ? `${data.due} to review and ${data.new} new today. Missed ones come back tomorrow, solved ones after 3 days, then ever later.`
            : `All done for today.${data.tomorrow ? ` ${data.tomorrow} come back tomorrow.` : ""}`,
          `Learned ${data.learned} of ${data.total}.`,
        );
      }
      if (critical?.total) lines.push(`Critical moments: you found the only good move in ${critical.found} of ${critical.total}.`);
      status.replaceChildren(lines.join(" "));
      if (data.due + data.new) {
        box.append(
          el("div", { class: "row" }, el("a", { class: "button primary", href: `${userHash(username)}/puzzles`, text: "Start today's puzzles" })),
        );
      }
    })
    .catch(() => box.remove());
  return box;
}
