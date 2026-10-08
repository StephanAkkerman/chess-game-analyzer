"""Configuration for the web app, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import chess.engine

from chess_analyzer.engine import (
    DEFAULT_DEPTH,
    DEFAULT_MAX_TIME,
    default_threads,
    describe_limit,
    make_limit,
)


def _env_int(name: str, default: int | None) -> int | None:
    value = os.environ.get(name)
    return int(value) if value else default


def _env_float(name: str, default: float | None) -> float | None:
    value = os.environ.get(name)
    return float(value) if value else default


@dataclass
class Settings:
    """Web app settings.

    Every field can be set with the environment variable of the same name in
    upper case, e.g. ``ANALYSIS_DEPTH=18``.

    By default each position is searched to depth 16 (stopping after 15
    seconds at most) with all CPU cores but one, at low priority. Set
    ``analysis_depth`` to 0 and ``analysis_time`` to search for a fixed time
    instead. Analyses in the browser use the same limits.

    ``browser_engine_dir`` holds the Stockfish.js builds that let browsers
    run the engine themselves (see ``deploy/fetch-stockfish-js.sh``).

    ``coach_url`` and ``coach_model`` point at an OpenAI-compatible API
    whose language model rewords move explanations (see
    :mod:`chess_analyzer.coach`). Without them the plain explanations are
    used.
    """

    data_dir: Path = Path("data")
    stockfish_path: str | None = None
    stockfish_threads: int | None = None
    stockfish_hash: int = 128
    engine_nice: int = 10
    analysis_depth: int | None = DEFAULT_DEPTH
    analysis_max_time: float | None = DEFAULT_MAX_TIME
    analysis_time: float | None = None
    workers: int = 1
    max_queue: int = 20
    max_plies: int = 400
    max_pgn_bytes: int = 100_000
    opening_book: str | None = None
    syzygy_path: str | None = None
    browser_engine_dir: str | None = None
    opening_explorer: str = "lichess"
    opening_plies: int = 30
    lichess_token: str | None = field(default=None, repr=False)
    access_code: str | None = field(default=None, repr=False)
    coach_url: str | None = None
    coach_model: str | None = None
    coach_api_key: str | None = field(default=None, repr=False)
    coach_timeout: float = 60.0

    @classmethod
    def from_env(cls) -> Settings:
        d = cls()
        return cls(
            data_dir=Path(os.environ.get("DATA_DIR", d.data_dir)),
            stockfish_path=os.environ.get("STOCKFISH_PATH") or None,
            stockfish_threads=_env_int("STOCKFISH_THREADS", None),
            stockfish_hash=_env_int("STOCKFISH_HASH", d.stockfish_hash),
            engine_nice=_env_int("ENGINE_NICE", d.engine_nice),
            analysis_depth=_env_int("ANALYSIS_DEPTH", d.analysis_depth),
            analysis_max_time=_env_float("ANALYSIS_MAX_TIME", d.analysis_max_time),
            analysis_time=_env_float("ANALYSIS_TIME", None),
            workers=_env_int("WORKERS", d.workers),
            max_queue=_env_int("MAX_QUEUE", d.max_queue),
            max_plies=_env_int("MAX_PLIES", d.max_plies),
            max_pgn_bytes=_env_int("MAX_PGN_BYTES", d.max_pgn_bytes),
            opening_book=os.environ.get("OPENING_BOOK") or None,
            syzygy_path=os.environ.get("SYZYGY_PATH") or None,
            browser_engine_dir=os.environ.get("BROWSER_ENGINE_DIR") or None,
            opening_explorer=os.environ.get("OPENING_EXPLORER", d.opening_explorer),
            opening_plies=_env_int("OPENING_PLIES", d.opening_plies),
            lichess_token=os.environ.get("LICHESS_TOKEN") or None,
            access_code=os.environ.get("ACCESS_CODE") or None,
            coach_url=os.environ.get("COACH_URL") or None,
            coach_model=os.environ.get("COACH_MODEL") or None,
            coach_api_key=os.environ.get("COACH_API_KEY") or None,
            coach_timeout=_env_float("COACH_TIMEOUT", d.coach_timeout),
        )

    @property
    def opening_label(self) -> str | None:
        """Name of the opening source that is used, or ``None``."""
        if self.opening_book and os.path.isfile(self.opening_book):
            return "Polyglot book"
        if self.opening_explorer == "masters":
            return "Lichess masters explorer"
        if self.opening_explorer == "lichess":
            return "Lichess opening explorer"
        return None

    @property
    def threads(self) -> int:
        """Engine threads per worker; by default all cores but one."""
        return self.stockfish_threads or default_threads(self.workers)

    @property
    def limit(self) -> chess.engine.Limit:
        return make_limit(
            depth=self.analysis_depth,
            time=self.analysis_time,
            max_time=self.analysis_max_time,
        )

    @property
    def engine_label(self) -> str:
        return describe_limit(self.limit)
