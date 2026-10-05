"""Background workers that run Stockfish analyses from a queue."""

from __future__ import annotations

import hashlib
import logging
import queue
import secrets
import threading
from collections.abc import Callable

import chess
import chess.engine
import chess.pgn

from chess_analyzer.engine import analyze_game, find_engine
from chess_analyzer.openings import (
    LichessExplorer,
    OpeningSource,
    PolyglotBook,
    find_deviation,
)
from chess_analyzer.parse import read_games
from chess_analyzer.web.serialize import result_to_dict
from chess_analyzer.web.settings import Settings
from chess_analyzer.web.store import DONE, FAILED, Job, Store

log = logging.getLogger(__name__)

EngineFactory = Callable[[], chess.engine.SimpleEngine]
OpeningFactory = Callable[[], OpeningSource | None]


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


def game_key(game: chess.pgn.Game, settings: Settings) -> str:
    """Identify a game and engine configuration, to reuse earlier results."""
    moves = " ".join(m.uci() for m in game.mainline_moves())
    parts = [
        game.board().fen(),
        moves,
        game.headers.get("Link", ""),
        settings.engine_label,
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def default_engine_factory(settings: Settings) -> EngineFactory:
    path = find_engine(settings.stockfish_path)
    if path is None:
        raise RuntimeError("Stockfish not found; set STOCKFISH_PATH.")

    def factory() -> chess.engine.SimpleEngine:
        engine = chess.engine.SimpleEngine.popen_uci(path)
        engine.configure(
            {"Threads": settings.stockfish_threads, "Hash": settings.stockfish_hash}
        )
        return engine

    return factory


def default_opening_factory(settings: Settings) -> OpeningFactory:
    def factory() -> OpeningSource | None:
        if settings.opening_book:
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
    engine_factory, opening_factory : callable, optional
        Create the engine and opening source for a worker. Default to Stockfish
        and the source configured in ``settings``.
    """

    def __init__(
        self,
        settings: Settings,
        store: Store,
        engine_factory: EngineFactory | None = None,
        opening_factory: OpeningFactory | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.engine_factory = engine_factory or default_engine_factory(settings)
        self.opening_factory = opening_factory or default_opening_factory(settings)
        self.limit = (
            chess.engine.Limit(depth=settings.analysis_depth)
            if settings.analysis_depth
            else chess.engine.Limit(time=settings.analysis_time)
        )
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._waiting: list[str] = []
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []

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

    def submit(self, pgn: str) -> Job:
        """Queue a game for analysis, or return an earlier analysis of it."""
        if len(pgn.encode()) > self.settings.max_pgn_bytes:
            raise InvalidGame("The PGN is too large.")
        game = parse_single_game(pgn, self.settings.max_plies)
        key = game_key(game, self.settings)
        if existing := self.store.find_by_key(key):
            return existing
        with self._lock:
            if len(self._waiting) >= self.settings.max_queue:
                raise QueueFull("The analysis queue is full; try again later.")
        total = sum(1 for _ in game.mainline_moves())
        job = self.store.create(secrets.token_urlsafe(8), key, pgn, total)
        self._enqueue(job.id)
        return job

    def _work(self) -> None:
        engine = None
        opening = None
        try:
            while (job_id := self._queue.get()) is not None:
                with self._lock:
                    self._waiting.remove(job_id)
                try:
                    if engine is None:
                        engine = self.engine_factory()
                    if opening is None:
                        opening = self.opening_factory()
                    self._run(job_id, engine, opening)
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
            if hasattr(opening, "close"):
                opening.close()

    def _run(
        self,
        job_id: str,
        engine: chess.engine.SimpleEngine,
        opening: OpeningSource | None,
    ) -> None:
        job = self.store.get(job_id)
        if job is None or job.status in (DONE, FAILED):
            return
        game = parse_single_game(job.pgn, self.settings.max_plies)
        self.store.set_running(job_id)

        deviation, opening_error = None, None
        if opening is not None:
            try:
                deviation = find_deviation(
                    game, opening, max_ply=self.settings.opening_plies
                )
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
        )
        result = result_to_dict(
            game,
            analysis,
            deviation,
            opening_checked=opening is not None and opening_error is None,
            opening_error=opening_error,
            engine_label=self.settings.engine_label,
        )
        self.store.set_done(job_id, result)
