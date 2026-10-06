"""Analyse games, with Stockfish on the server or in the user's browser.

Server analyses wait in a queue for background workers. Each worker keeps one
Stockfish process, opening source and set of endgame tablebases open between
jobs. Book moves and tablebase positions are not searched, which keeps the
engine work small enough for a Raspberry Pi.

Device analyses leave the searching to the browser (see
:mod:`chess_analyzer.web.device`) and need no worker: the server does its
part when the analysis is submitted and each time searches come back.
"""

from __future__ import annotations

import hashlib
import logging
import os
import queue
import secrets
import threading
from collections.abc import Callable
from pathlib import Path

import chess
import chess.engine
import chess.pgn

from chess_analyzer.engine import (
    Tablebase,
    analyze_game,
    engine_major_version,
    find_engine,
    open_engine,
    open_tablebase,
)
from chess_analyzer.openings import (
    LichessExplorer,
    OpeningSource,
    PolyglotBook,
    book_plies,
    find_deviation,
)
from chess_analyzer.parse import read_games
from chess_analyzer.serialize import deviation_to_dict, result_to_dict
from chess_analyzer.web.device import DeviceEngine, parse_evaluation
from chess_analyzer.web.settings import Settings
from chess_analyzer.web.store import DEVICE, DONE, FAILED, Job, Store

log = logging.getLogger(__name__)

EngineFactory = Callable[[], chess.engine.SimpleEngine]
OpeningFactory = Callable[[], OpeningSource | None]
TablebaseFactory = Callable[[], Tablebase | None]


# Part of the key that identifies an analysis. Raise it when results gain new
# information, so games are analysed again rather than served from old results.
ANALYSIS_VERSION = 3


class InvalidGame(ValueError):
    """The submitted PGN cannot be analysed."""


class QueueFull(RuntimeError):
    """Too many analyses are waiting already."""


def parse_single_game(pgn: str, max_plies: int) -> chess.pgn.Game:
    """Parse and validate the first game in ``pgn``."""
    games = read_games(pgn)
    if not games:
        raise InvalidGame("No game found in the PGN.")
    game = games[0]
    if game.errors:
        raise InvalidGame(f"Could not read the PGN: {game.errors[0]}")
    variant = game.headers.get("Variant", "Standard").lower()
    if variant not in ("standard", "chess", "from position"):
        raise InvalidGame(f"Variant '{variant}' is not supported.")
    plies = sum(1 for _ in game.mainline_moves())
    if plies == 0:
        raise InvalidGame("The game has no moves.")
    if plies > max_plies:
        raise InvalidGame(f"The game is too long ({plies} half-moves).")
    return game


def game_key(game: chess.pgn.Game, settings: Settings, device: bool = False) -> str:
    """Identify a game and engine configuration, to reuse earlier results.

    ``device`` marks analyses with the engine in the browser, which is not
    the same engine as the server's.
    """
    moves = " ".join(m.uci() for m in game.mainline_moves())
    parts = [
        f"v{ANALYSIS_VERSION}",
        game.board().fen(),
        moves,
        game.headers.get("Link", ""),
        settings.engine_label,
        settings.opening_label or "",
        "syzygy" if has_tablebases(settings.syzygy_path) else "",
    ]
    if device:
        parts.append("device")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def default_engine_factory(settings: Settings) -> EngineFactory:
    syzygy = settings.syzygy_path if has_tablebases(settings.syzygy_path) else None

    def factory() -> chess.engine.SimpleEngine:
        # Looked up here rather than up front: without Stockfish on the
        # server, analyses in the browser still work.
        path = find_engine(settings.stockfish_path)
        if path is None:
            raise RuntimeError("Stockfish not found; set STOCKFISH_PATH.")
        return open_engine(
            path,
            threads=settings.threads,
            hash_mb=settings.stockfish_hash,
            syzygy_path=syzygy,
            nice=settings.engine_nice,
        )

    return factory


def has_tablebases(path: str | None) -> bool:
    """Return whether ``path`` is a directory containing Syzygy WDL tables."""
    return bool(path) and Path(path).is_dir() and any(Path(path).glob("*.rtbw"))


def default_tablebase_factory(settings: Settings) -> TablebaseFactory:
    def factory() -> Tablebase | None:
        if not has_tablebases(settings.syzygy_path):
            return None
        return open_tablebase(settings.syzygy_path)

    return factory


def default_opening_factory(settings: Settings) -> OpeningFactory:
    def factory() -> OpeningSource | None:
        if settings.opening_book and os.path.isfile(settings.opening_book):
            return PolyglotBook(settings.opening_book)
        if settings.opening_explorer in ("lichess", "masters"):
            return LichessExplorer(
                database=settings.opening_explorer, token=settings.lichess_token
            )
        return None

    return factory


class AnalysisService:
    """Queue analyses and run them on worker threads.

    Each worker owns one engine process and one opening source.

    Parameters
    ----------
    settings : Settings
        App settings.
    store : Store
        Where jobs and results are kept.
    engine_factory, opening_factory, tablebase_factory : callable, optional
        Create the engine, opening source and tablebases for a worker. Default
        to Stockfish and the sources configured in ``settings``.
    """

    def __init__(
        self,
        settings: Settings,
        store: Store,
        engine_factory: EngineFactory | None = None,
        opening_factory: OpeningFactory | None = None,
        tablebase_factory: TablebaseFactory | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.engine_factory = engine_factory or default_engine_factory(settings)
        self.opening_factory = opening_factory or default_opening_factory(settings)
        self.tablebase_factory = tablebase_factory or default_tablebase_factory(
            settings
        )
        self.limit = settings.limit
        self.engine_name: str | None = None
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._waiting: list[str] = []
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        # Device analyses share one opening source and set of tablebases,
        # used by one request at a time.
        self._device_lock = threading.Lock()
        self._device_opening: OpeningSource | None = None
        self._device_tablebase: Tablebase | None = None
        self._device_ready = False

    def start(self) -> None:
        # Jobs that were queued or running when the app stopped run again.
        for job_id in self.store.pending_ids():
            self._enqueue(job_id)
        for i in range(self.settings.workers):
            thread = threading.Thread(
                target=self._work, name=f"analysis-{i}", daemon=True
            )
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        for _ in self._threads:
            self._queue.put(None)
        for thread in self._threads:
            thread.join(timeout=10)
        self._threads.clear()
        with self._device_lock:
            for resource in (self._device_opening, self._device_tablebase):
                if hasattr(resource, "close"):
                    resource.close()
            self._device_ready = False

    def _enqueue(self, job_id: str) -> None:
        with self._lock:
            self._waiting.append(job_id)
        self._queue.put(job_id)

    def queue_position(self, job_id: str) -> int | None:
        """Return how many jobs are ahead of ``job_id``, if it is waiting."""
        with self._lock:
            if job_id in self._waiting:
                return self._waiting.index(job_id)
        return None

    def submit(self, pgn: str, device: bool = False) -> Job:
        """Queue a game for analysis, or return an earlier analysis of it.

        With ``device`` the browser runs the engine: the job then waits for
        the searches it lists, see :meth:`submit_evaluations`. A finished
        server analysis of the game is returned instead, if there is one.
        """
        if len(pgn.encode()) > self.settings.max_pgn_bytes:
            raise InvalidGame("The PGN is too large.")
        game = parse_single_game(pgn, self.settings.max_plies)
        key = game_key(game, self.settings)
        if device:
            server = self.store.find_by_key(key)
            if server is not None and server.status == DONE:
                return server
            key = game_key(game, self.settings, device=True)
        if existing := self.store.find_by_key(key):
            return existing
        if device:
            return self._create_device_job(game, pgn, key)
        with self._lock:
            if len(self._waiting) >= self.settings.max_queue:
                raise QueueFull("The analysis queue is full; try again later.")
        total = sum(1 for _ in game.mainline_moves())
        job = self.store.create(secrets.token_urlsafe(8), key, pgn, total)
        self._enqueue(job.id)
        return job

    def _start_engine(self) -> chess.engine.SimpleEngine:
        engine = self.engine_factory()
        name = getattr(engine, "id", {}).get("name")
        if name and name != self.engine_name:
            self.engine_name = name
            log.info("Started %s", name)
            major = engine_major_version(engine)
            if name.startswith("Stockfish") and major is not None and major < 16:
                log.warning(
                    "%s is old; Stockfish 16 or newer evaluates with NNUE only "
                    "and is much stronger.",
                    name,
                )
        return engine

    def _work(self) -> None:
        engine = None
        opening = None
        tablebase = None
        tablebase_opened = False
        try:
            # Start the engine right away, so a broken setup shows in the logs
            # at startup rather than on the first analysis.
            try:
                engine = self._start_engine()
            except RuntimeError as exc:  # no Stockfish on the server
                log.warning("%s Only analyses in the browser will work.", exc)
            except Exception:
                log.exception("Could not start the engine")
            while (job_id := self._queue.get()) is not None:
                with self._lock:
                    self._waiting.remove(job_id)
                try:
                    if engine is None:
                        engine = self._start_engine()
                    if opening is None:
                        opening = self.opening_factory()
                    if not tablebase_opened:
                        tablebase = self.tablebase_factory()
                        tablebase_opened = True
                    self._run(job_id, engine, opening, tablebase)
                except chess.engine.EngineTerminatedError:
                    log.exception("Engine died while analysing %s", job_id)
                    self.store.set_failed(job_id, "The engine stopped unexpectedly.")
                    engine = None
                except Exception as exc:
                    log.exception("Analysis %s failed", job_id)
                    self.store.set_failed(job_id, str(exc) or type(exc).__name__)
        finally:
            if engine is not None:
                engine.quit()
            for resource in (opening, tablebase):
                if hasattr(resource, "close"):
                    resource.close()

    def _run(
        self,
        job_id: str,
        engine: chess.engine.SimpleEngine,
        opening: OpeningSource | None,
        tablebase: Tablebase | None = None,
    ) -> None:
        job = self.store.get(job_id)
        if job is None or job.status in (DONE, FAILED):
            return
        game = parse_single_game(job.pgn, self.settings.max_plies)
        self.store.set_running(job_id)

        deviation, opening_error, book = None, None, set()
        if opening is not None:
            try:
                deviation = find_deviation(
                    game, opening, max_ply=self.settings.opening_plies
                )
                book = book_plies(game, deviation, self.settings.opening_plies)
            except Exception as exc:  # noqa: BLE001 - the engine part still works
                log.warning("Opening lookup failed for %s: %s", job_id, exc)
                opening_error = "The opening explorer could not be reached."

        analysis = analyze_game(
            game,
            engine,
            self.limit,
            on_progress=lambda done, total: self.store.set_progress(
                job_id, done, total
            ),
            book_plies=book,
            tablebase=tablebase,
        )
        result = result_to_dict(
            game,
            analysis,
            deviation,
            opening_checked=opening is not None and opening_error is None,
            opening_error=opening_error,
            engine_label=self.settings.engine_label,
            engine_name=self.engine_name,
        )
        self.store.set_done(job_id, result)

    # ---------------------------------------------------------------- device

    def _device_resources(self) -> tuple[OpeningSource | None, Tablebase | None]:
        """Open the device analyses' opening source and tablebases once."""
        if not self._device_ready:
            self._device_opening = self.opening_factory()
            self._device_tablebase = self.tablebase_factory()
            self._device_ready = True
        return self._device_opening, self._device_tablebase

    def _create_device_job(self, game: chess.pgn.Game, pgn: str, key: str) -> Job:
        with self._device_lock:
            opening, _ = self._device_resources()
            deviation, opening_error, book = None, None, set()
            if opening is not None:
                try:
                    deviation = find_deviation(
                        game, opening, max_ply=self.settings.opening_plies
                    )
                    book = book_plies(game, deviation, self.settings.opening_plies)
                except Exception as exc:  # noqa: BLE001 - the engine part still works
                    log.warning("Opening lookup failed: %s", exc)
                    opening_error = "The opening explorer could not be reached."
            state = {
                # The opening part of the result, so it is looked up only once.
                "opening": {
                    "checked": opening is not None and opening_error is None,
                    "error": opening_error,
                    "deviation": deviation_to_dict(deviation),
                },
                "book": sorted(book),
                "evaluations": {},
                "searches": [],
                "engine": None,
            }
            total = sum(1 for _ in game.mainline_moves())
            job = self.store.create(
                secrets.token_urlsafe(8), key, pgn, total, status=DEVICE, state=state
            )
            return self._advance(job, state)

    def submit_evaluations(
        self, job_id: str, evaluations: list[dict], engine_name: str | None = None
    ) -> Job | None:
        """Add evaluations from the browser to a device analysis.

        Evaluations are dicts with the search ``id``, the ``best`` move and a
        ``cp`` or ``mate`` score; see
        :func:`chess_analyzer.web.device.parse_evaluation`. Evaluations of
        searches the job is not waiting for are ignored, so a second tab
        working on the same game does no harm. Once every search of a round
        is answered, the game is analysed again: that finishes the job or
        asks for the next round of searches. Returns ``None`` for an unknown
        job.
        """
        with self._device_lock:
            job = self.store.get(job_id)
            if job is None or job.status != DEVICE:
                return job
            state = job.state
            searches = {s["id"]: s for s in state["searches"]}
            for data in evaluations:
                if (search := searches.get(data.get("id"))) is not None:
                    state["evaluations"][search["id"]] = parse_evaluation(search, data)
            if engine_name:
                state["engine"] = engine_name
            state["searches"] = [
                s for s in state["searches"] if s["id"] not in state["evaluations"]
            ]
            if state["searches"]:
                answered = len(state["evaluations"])
                self.store.set_state(
                    job_id, state, answered, answered + len(state["searches"])
                )
                return self.store.get(job_id)
            return self._advance(job, state)

    def _advance(self, job: Job, state: dict) -> Job:
        """Analyse a device job with the evaluations so far.

        Saves the searches that are still missing, or the result once there
        are none. Call with the device lock held.
        """
        try:
            _, tablebase = self._device_resources()
            game = parse_single_game(job.pgn, self.settings.max_plies)
            engine = DeviceEngine(state["evaluations"])
            analysis = analyze_game(
                game,
                engine,
                self.limit,
                book_plies=state["book"],
                tablebase=tablebase,
            )
            if engine.missing:
                state["searches"] = list(engine.missing.values())
                answered = len(state["evaluations"])
                self.store.set_state(
                    job.id, state, answered, answered + len(engine.missing)
                )
                return self.store.get(job.id)
            result = result_to_dict(
                game,
                analysis,
                None,
                opening_checked=state["opening"]["checked"],
                opening_error=state["opening"]["error"],
                engine_label=f"{self.settings.engine_label}, on your device",
                engine_name=state["engine"],
            )
            result["opening"]["deviation"] = state["opening"]["deviation"]
            self.store.set_done(job.id, result)
        except Exception as exc:
            log.exception("Device analysis %s failed", job.id)
            self.store.set_failed(job.id, str(exc) or type(exc).__name__)
        return self.store.get(job.id)
