"""Evaluate games move by move with a UCI engine such as Stockfish."""

from __future__ import annotations

import os
import shutil
from collections import Counter
from dataclasses import dataclass, field

import chess
import chess.engine
import chess.pgn

# Mate scores are mapped to this many centipawns.
MATE_SCORE = 10_000
# Evaluations are capped at this value before computing centipawn loss, so that
# e.g. dropping from "mate in 5" to "+15" in a won position is not a blunder.
EVAL_CAP = 1_000

INACCURACY = 50
MISTAKE = 100
BLUNDER = 300

CLASSIFICATIONS = ("best", "good", "inaccuracy", "mistake", "blunder")


def find_engine(path: str | None = None) -> str | None:
    """Locate a Stockfish binary.

    Checks ``path``, then the ``STOCKFISH_PATH`` environment variable, then
    ``stockfish`` on the ``PATH`` (and ``/usr/games`` where Debian installs it).
    """
    candidates = [path, os.environ.get("STOCKFISH_PATH")]
    for candidate in candidates:
        if candidate:
            return shutil.which(candidate) or (
                candidate if os.path.isfile(candidate) else None
            )
    return shutil.which("stockfish") or shutil.which("/usr/games/stockfish")


def classify(cp_loss: int, is_best: bool) -> str:
    """Classify a move by its centipawn loss."""
    if is_best:
        return "best"
    if cp_loss >= BLUNDER:
        return "blunder"
    if cp_loss >= MISTAKE:
        return "mistake"
    if cp_loss >= INACCURACY:
        return "inaccuracy"
    return "good"


@dataclass
class MoveAnalysis:
    """Engine verdict on a single move.

    Evaluations are in centipawns from White's point of view.
    """

    ply: int
    color: chess.Color
    san: str
    best_san: str
    eval_before: int
    eval_after: int
    cp_loss: int
    classification: str
    fen_before: str

    @property
    def move_number(self) -> int:
        return (self.ply + 1) // 2

    @property
    def label(self) -> str:
        """Move in book notation, e.g. ``12.Nf3`` or ``12...Nf6``."""
        dots = "." if self.color == chess.WHITE else "..."
        return f"{self.move_number}{dots}{self.san}"


@dataclass
class GameAnalysis:
    """All move analyses for one game."""

    moves: list[MoveAnalysis] = field(default_factory=list)

    def for_color(self, color: chess.Color) -> list[MoveAnalysis]:
        return [m for m in self.moves if m.color == color]

    def average_cp_loss(self, color: chess.Color) -> float:
        moves = self.for_color(color)
        return sum(m.cp_loss for m in moves) / len(moves) if moves else 0.0

    def counts(self, color: chess.Color) -> Counter:
        return Counter(m.classification for m in self.for_color(color))

    def worst_moves(self, color: chess.Color, n: int = 3) -> list[MoveAnalysis]:
        """Return the ``n`` costliest mistakes, inaccuracies excluded."""
        bad = [
            m
            for m in self.for_color(color)
            if m.classification in ("mistake", "blunder")
        ]
        return sorted(bad, key=lambda m: m.cp_loss, reverse=True)[:n]


def _terminal_score(board: chess.Board) -> int:
    if board.is_checkmate():
        return -MATE_SCORE if board.turn == chess.WHITE else MATE_SCORE
    return 0


def _evaluate(
    engine: chess.engine.SimpleEngine, board: chess.Board, limit: chess.engine.Limit
) -> tuple[int, chess.Move | None]:
    """Return (White-POV centipawns, best move) for a position."""
    if board.is_game_over():
        return _terminal_score(board), None
    info = engine.analyse(board, limit)
    score = info["score"].white().score(mate_score=MATE_SCORE)
    pv = info.get("pv") or [None]
    return score, pv[0]


def _cap(cp: int) -> int:
    return max(-EVAL_CAP, min(EVAL_CAP, cp))


def analyze_game(
    game: chess.pgn.Game,
    engine: chess.engine.SimpleEngine,
    limit: chess.engine.Limit | None = None,
) -> GameAnalysis:
    """Evaluate every move of a game.

    Each position is searched once: the search before a move gives the best
    move and its score, the search after it gives the score of the move that
    was actually played.

    Parameters
    ----------
    game : chess.pgn.Game
        The game to analyse.
    engine : chess.engine.SimpleEngine
        An open UCI engine.
    limit : chess.engine.Limit, optional
        Search limit per position. Defaults to 0.5 seconds.

    Returns
    -------
    GameAnalysis
        One entry per half-move.
    """
    limit = limit or chess.engine.Limit(time=0.5)
    board = game.board()
    analysis = GameAnalysis()

    score_before, best = _evaluate(engine, board, limit)
    for ply, move in enumerate(game.mainline_moves(), start=1):
        color = board.turn
        san = board.san(move)
        best_san = board.san(best) if best else ""
        fen_before = board.fen()

        board.push(move)
        score_after, next_best = _evaluate(engine, board, limit)

        sign = 1 if color == chess.WHITE else -1
        # Playing the engine's own choice costs nothing; any difference between
        # the two searches is just noise.
        cp_loss = (
            0
            if move == best
            else max(0, sign * (_cap(score_before) - _cap(score_after)))
        )
        analysis.moves.append(
            MoveAnalysis(
                ply=ply,
                color=color,
                san=san,
                best_san=best_san,
                eval_before=score_before,
                eval_after=score_after,
                cp_loss=cp_loss,
                classification=classify(cp_loss, move == best),
                fen_before=fen_before,
            )
        )
        score_before, best = score_after, next_best
    return analysis
