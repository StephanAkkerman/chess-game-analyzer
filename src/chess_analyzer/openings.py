"""Find where a game left opening theory and what theory recommends instead.

Two sources of theory are supported: a local Polyglot opening book (``.bin``)
and the Lichess Opening Explorer API.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

import chess
import chess.pgn
import chess.polyglot
import requests

if TYPE_CHECKING:
    from typing_extensions import Self

LICHESS_EXPLORER_URL = "https://explorer.lichess.ovh"


@dataclass
class BookMove:
    """A move known to an opening source.

    ``games`` is the number of games for explorer data, or the move weight for
    a Polyglot book. Result counts are only known for explorer data.
    """

    uci: str
    san: str
    games: int
    white: int | None = None
    draws: int | None = None
    black: int | None = None

    def score_for(self, color: chess.Color) -> float | None:
        """Return the expected score (0-1) for ``color``, if results are known."""
        if self.white is None or self.draws is None or self.black is None:
            return None
        total = self.white + self.draws + self.black
        if total == 0:
            return None
        wins = self.white if color == chess.WHITE else self.black
        return (wins + 0.5 * self.draws) / total


class OpeningSource(Protocol):
    def moves(self, board: chess.Board) -> list[BookMove]:
        """Return the book moves for the side to move in ``board``."""


class PolyglotBook:
    """Opening theory from a local Polyglot ``.bin`` book.

    Parameters
    ----------
    path : str
        Path to the book file.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._reader = chess.polyglot.open_reader(path)

    def close(self) -> None:
        self._reader.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def moves(self, board: chess.Board) -> list[BookMove]:
        weights: dict[chess.Move, int] = {}
        for entry in self._reader.find_all(board):
            weights[entry.move] = weights.get(entry.move, 0) + entry.weight
        return [
            BookMove(uci=move.uci(), san=board.san(move), games=weight)
            for move, weight in weights.items()
        ]


class LichessExplorer:
    """Opening theory from the Lichess Opening Explorer.

    Parameters
    ----------
    database : {"lichess", "masters"}
        ``lichess`` uses games played on Lichess, ``masters`` uses OTB games
        between titled players.
    speeds, ratings : list, optional
        Filters for the ``lichess`` database.
    min_games : int, optional
        Moves played in fewer games are not treated as theory.
    token : str, optional
        Lichess API token, sent as a bearer token when given.
    session : requests.Session, optional
        Session to reuse.
    """

    def __init__(
        self,
        database: str = "lichess",
        speeds: list[str] | None = None,
        ratings: list[int] | None = None,
        min_games: int = 50,
        token: str | None = None,
        session: requests.Session | None = None,
        timeout: float = 30,
    ) -> None:
        if database not in ("lichess", "masters"):
            raise ValueError(f"Unknown explorer database: {database}")
        self.database = database
        self.speeds = speeds or ["blitz", "rapid", "classical"]
        self.ratings = ratings or [1600, 1800, 2000, 2200, 2500]
        self.min_games = min_games
        self.session = session or requests.Session()
        if token:
            self.session.headers.update({"Authorization": f"Bearer {token}"})
        self.timeout = timeout
        self._cache: dict[str, list[BookMove]] = {}

    def _params(self, board: chess.Board) -> dict:
        params = {"fen": board.fen(), "moves": 12, "topGames": 0}
        if self.database == "lichess":
            params.update(
                speeds=",".join(self.speeds),
                ratings=",".join(str(r) for r in self.ratings),
                recentGames=0,
            )
        return params

    def _get(self, params: dict) -> dict:
        url = f"{LICHESS_EXPLORER_URL}/{self.database}"
        response = self.session.get(url, params=params, timeout=self.timeout)
        if response.status_code == 429:
            # Lichess asks clients to wait a full minute after a 429.
            time.sleep(60)
            response = self.session.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def moves(self, board: chess.Board) -> list[BookMove]:
        key = board.fen()
        if key not in self._cache:
            data = self._get(self._params(board))
            moves = []
            for m in data.get("moves", []):
                games = m["white"] + m["draws"] + m["black"]
                if games >= self.min_games:
                    moves.append(
                        BookMove(
                            # Normalises king-takes-rook castling (e1h1 -> e1g1).
                            uci=board.parse_uci(m["uci"]).uci(),
                            san=m["san"],
                            games=games,
                            white=m["white"],
                            draws=m["draws"],
                            black=m["black"],
                        )
                    )
            self._cache[key] = moves
        return self._cache[key]


def rank_moves(moves: list[BookMove], color: chess.Color) -> list[BookMove]:
    """Order book moves best-first for ``color``.

    Moves with known results are ranked by expected score, then popularity;
    otherwise by popularity (or Polyglot weight) alone.
    """
    return sorted(
        moves,
        key=lambda m: (
            m.score_for(color) if m.score_for(color) is not None else -1.0,
            m.games,
        ),
        reverse=True,
    )


@dataclass
class OpeningDeviation:
    """The first move of a game that was not in the opening source."""

    ply: int
    color: chess.Color
    played_san: str
    fen: str
    alternatives: list[BookMove] = field(default_factory=list)

    @property
    def move_number(self) -> int:
        return (self.ply + 1) // 2

    @property
    def label(self) -> str:
        dots = "." if self.color == chess.WHITE else "..."
        return f"{self.move_number}{dots}{self.played_san}"


def find_deviation(
    game: chess.pgn.Game, source: OpeningSource, max_ply: int = 30
) -> OpeningDeviation | None:
    """Return the first move that left theory, searching the first ``max_ply``.

    Returns ``None`` if the game stayed in book for ``max_ply`` half-moves, or
    ended before leaving it. The alternatives are the book moves in that
    position, best first for the side that deviated.
    """
    board = game.board()
    for ply, move in enumerate(game.mainline_moves(), start=1):
        if ply > max_ply:
            return None
        book = source.moves(board)
        if move.uci() not in {m.uci for m in book}:
            return OpeningDeviation(
                ply=ply,
                color=board.turn,
                played_san=board.san(move),
                fen=board.fen(),
                alternatives=rank_moves(book, board.turn),
            )
        board.push(move)
    return None
