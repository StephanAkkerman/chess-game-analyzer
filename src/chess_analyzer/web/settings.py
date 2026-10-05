"""Configuration for the web app, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_int(name: str, default: int | None) -> int | None:
    value = os.environ.get(name)
    return int(value) if value else default


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    return float(value) if value else default


@dataclass
class Settings:
    """Web app settings.

    Every field can be set with the environment variable of the same name in
    upper case, e.g. ``ANALYSIS_TIME=0.3``.
    """

    data_dir: Path = Path("data")
    stockfish_path: str | None = None
    stockfish_threads: int = 1
    stockfish_hash: int = 64
    analysis_time: float = 0.3
    analysis_depth: int | None = None
    workers: int = 1
    max_queue: int = 20
    max_plies: int = 400
    max_pgn_bytes: int = 100_000
    opening_book: str | None = None
    opening_explorer: str = "lichess"
    opening_plies: int = 30
    lichess_token: str | None = field(default=None, repr=False)
    access_code: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls) -> Settings:
        d = cls()
        return cls(
            data_dir=Path(os.environ.get("DATA_DIR", d.data_dir)),
            stockfish_path=os.environ.get("STOCKFISH_PATH") or None,
            stockfish_threads=_env_int("STOCKFISH_THREADS", d.stockfish_threads),
            stockfish_hash=_env_int("STOCKFISH_HASH", d.stockfish_hash),
            analysis_time=_env_float("ANALYSIS_TIME", d.analysis_time),
            analysis_depth=_env_int("ANALYSIS_DEPTH", None),
            workers=_env_int("WORKERS", d.workers),
            max_queue=_env_int("MAX_QUEUE", d.max_queue),
            max_plies=_env_int("MAX_PLIES", d.max_plies),
            max_pgn_bytes=_env_int("MAX_PGN_BYTES", d.max_pgn_bytes),
            opening_book=os.environ.get("OPENING_BOOK") or None,
            opening_explorer=os.environ.get("OPENING_EXPLORER", d.opening_explorer),
            opening_plies=_env_int("OPENING_PLIES", d.opening_plies),
            lichess_token=os.environ.get("LICHESS_TOKEN") or None,
            access_code=os.environ.get("ACCESS_CODE") or None,
        )

    @property
    def engine_label(self) -> str:
        if self.analysis_depth:
            return f"depth {self.analysis_depth}"
        return f"{self.analysis_time:g}s per move"
