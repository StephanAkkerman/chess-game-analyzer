// Stockfish in the browser, for analyses that run on the user's device.
// The server says which positions to search; this module searches them with
// Stockfish.js (WebAssembly, in a Web Worker) and returns the results.

// Whether this browser can run the engine at all.
export function deviceSupported() {
  return typeof WebAssembly === "object" && typeof Worker === "function";
}

// Several threads need SharedArrayBuffer, which browsers only allow on
// cross-origin isolated pages (the server sends the headers for that).
function canUseThreads() {
  return Boolean(globalThis.crossOriginIsolated) && (navigator.hardwareConcurrency || 1) > 1;
}

export class BrowserEngine {
  // `files` is the server's config.device_engine: { single, threaded } URLs.
  constructor(files) {
    this.threaded = Boolean(files.threaded) && canUseThreads();
    this.url = this.threaded ? files.threaded : files.single || files.threaded;
    this.threads = this.threaded ? Math.min(8, Math.max(1, navigator.hardwareConcurrency - 1)) : 1;
    this.name = "Stockfish";
    this.worker = null;
    this.listener = null;
    this.failure = null;
  }

  // Start the worker and wait until the engine is ready. Rejects if the
  // engine cannot load (no WebAssembly, download failed, ...).
  start() {
    this.ready ??= this.boot().catch((err) => {
      this.stop();
      throw err;
    });
    return this.ready;
  }

  async boot() {
    if (!this.url) throw new Error("This browser cannot run the engine.");
    this.worker = new Worker(this.url);
    this.worker.onmessage = (event) => {
      const line = typeof event.data === "string" ? event.data : String(event.data);
      if (this.listener) this.listener(line);
    };
    this.worker.onerror = (event) => {
      event.preventDefault?.();
      this.fail(new Error(`The engine could not start: ${event.message || "unknown error"}.`));
    };
    await this.waitFor("uci", (line) => {
      const name = line.match(/^id name (.+)$/);
      if (name) this.name = name[1].trim();
      return line === "uciok";
    });
    if (this.threads > 1) this.send(`setoption name Threads value ${this.threads}`);
    this.send("setoption name Hash value 32");
    await this.waitFor("isready", (line) => line === "readyok");
  }

  stop() {
    this.worker?.terminate();
    this.worker = null;
  }

  send(command) {
    this.worker.postMessage(command);
  }

  fail(error) {
    this.failure = error;
    this.listener?.(null);
  }

  // Send `command` and resolve once `done(line)` returns true for a line.
  waitFor(command, done) {
    return new Promise((resolve, reject) => {
      this.listener = (line) => {
        if (line === null) {
          this.listener = null;
          reject(this.failure);
        } else if (done(line)) {
          this.listener = null;
          resolve();
        }
      };
      if (this.failure) this.listener(null);
      else this.send(command);
    });
  }

  // Search one position: `search` comes from the server ({ fen, moves,
  // searchmoves }), `limit` is { depth, movetime }. Resolves to the best move,
  // its score from the side to move's point of view and the engine's line,
  // as { best, cp, pv } or { best, mate, pv }.
  async search(search, limit) {
    await this.start();
    const moves = search.moves.length ? ` moves ${search.moves.join(" ")}` : "";
    this.send(`position fen ${search.fen}${moves}`);
    let go = "go";
    if (limit.depth) go += ` depth ${limit.depth}`;
    if (limit.movetime || !limit.depth) go += ` movetime ${limit.movetime || 500}`;
    if (search.searchmoves.length) go += ` searchmoves ${search.searchmoves.join(" ")}`;

    let score = null;
    let best = null;
    let pv = [];
    await this.waitFor(go, (line) => {
      if (line.startsWith("info ") && / pv /.test(line)) {
        const multipv = line.match(/ multipv (\d+)/);
        const found = line.match(/ score (cp|mate) (-?\d+)( lowerbound| upperbound)?/);
        if (found && (!multipv || multipv[1] === "1")) {
          // A bound is only kept until a real score arrives.
          if (!found[3] || !score || score.bound) {
            score = { [found[1]]: Number(found[2]), bound: Boolean(found[3]) };
            pv = line.split(" pv ")[1].trim().split(/\s+/);
          }
        }
        return false;
      }
      const done = line.match(/^bestmove (\S+)/);
      if (done) best = done[1];
      return Boolean(done);
    });
    if (!best || best === "(none)" || !score) throw new Error("The engine found no move.");
    const { bound, ...value } = score;
    return { best, ...value, pv: pv[0] === best ? pv.slice(0, 12) : [best] };
  }
}
