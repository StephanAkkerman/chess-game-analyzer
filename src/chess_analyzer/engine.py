"""Evaluate games move by move, using the engine only where it is needed.

Each position is evaluated by the cheapest source that can answer it:

1. Opening book moves are not evaluated at all.
2. Positions with few pieces are looked up in Syzygy endgame tablebases.
3. A position with a single legal move takes the evaluation of the next one.
4. Everything else is searched by a UCI engine such as Stockfish.
"""

from __future__ import annotations

import os
import shutil
from collections import Counter
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from typing import Protocol

import chess
import chess.engine
import chess.pgn
import chess.syzygy

from chess_analyzer import insights

# Mate scores are mapped to this many centipawns.
MATE_SCORE = 10_000
# Evaluations are capped at this value before computing centipawn loss, so that
# e.g. dropping from "mate in 5" to "+15" in a won position is not a blunder.
EVAL_CAP = 1_000
# Tablebase wins and losses. Above EVAL_CAP so they count fully in losses, but
# well below MATE_SCORE so they are not shown as a mate.
TABLEBASE_WIN = 2_000
# Scores at least this large are forced mates.
MATE_THRESHOLD = MATE_SCORE - 500

INACCURACY = 50
MISTAKE = 100
BLUNDER = 300

CLASSIFICATIONS = (
    "book",
    "forced",
    "best",
    "good",
    "inaccuracy",
    "mistake",
    "blunder",
)
# How a position was evaluated.
ENGINE, TABLEBASE, FORCED, TERMINAL, BOOK = (
    "engine",
    "tablebase",
    "forced",
    "terminal",
    "book",
)


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
    """Verdict on a single move.

    Evaluations are in centipawns from White's point of view, or ``None`` for
    book positions that were never evaluated. ``source`` and
    ``source_before`` say how ``eval_after`` and ``eval_before`` were obtained.

    ``reply_uci`` and ``reply_san`` give the opponent's best reply, ``phase``
    the game phase and ``category`` what kind of error a mistake or blunder
    was (see :mod:`chess_analyzer.insights`). ``clock`` and ``time_spent``
    come from the PGN's clock comments, in seconds; ``time_flag`` marks moves
    that point to a time-management problem.
    """

    ply: int
    color: chess.Color
    san: str
    best_san: str
    eval_before: int | None
    eval_after: int | None
    cp_loss: int
    classification: str
    fen_before: str
    uci: str = ""
    best_uci: str = ""
    fen_after: str = ""
    source: str = ENGINE
    source_before: str = ENGINE
    reply_uci: str = ""
    reply_san: str = ""
    phase: str = ""
    category: str = ""
    clock: float | None = None
    time_spent: float | None = None
    time_flag: str = ""

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
    """All move analyses for one game.

    ``sources`` counts how each position of the game was evaluated.
    """

    moves: list[MoveAnalysis] = field(default_factory=list)
    sources: Counter = field(default_factory=Counter)

    def for_color(self, color: chess.Color) -> list[MoveAnalysis]:
        return [m for m in self.moves if m.color == color]

    def average_cp_loss(self, color: chess.Color) -> float:
        """Average loss over the moves that were a real decision.

        Book moves and forced moves are left out.
        """
        moves = [
            m
            for m in self.for_color(color)
            if m.classification not in ("book", "forced")
        ]
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

    def categories(self, color: chess.Color) -> Counter:
        """Count the kinds of mistakes and blunders ``color`` made."""
        return Counter(m.category for m in self.for_color(color) if m.category)

    def phase_cp_loss(self, color: chess.Color) -> dict[str, tuple[float, int]]:
        """Return the average centipawn loss and move count per game phase.

        Book and forced moves are left out, as in :meth:`average_cp_loss`.
        """
        losses: dict[str, list[int]] = {}
        for m in self.for_color(color):
            if m.classification not in ("book", "forced") and m.phase:
                losses.setdefault(m.phase, []).append(m.cp_loss)
        return {
            phase: (sum(losses[phase]) / len(losses[phase]), len(losses[phase]))
            for phase in insights.PHASES
            if phase in losses
        }

    def time_summary(self, color: chess.Color) -> dict | None:
        """Summarise how ``color`` used the clock, or ``None`` without clocks.

        Counts the moves with each time flag, and the mistakes and blunders
        in total, so the share made in time trouble can be computed.
        """
        timed = [m for m in self.for_color(color) if m.time_spent is not None]
        if not timed:
            return None
        flags = Counter(m.time_flag for m in timed if m.time_flag)
        return {
            "moves": len(timed),
            "average": sum(m.time_spent for m in timed) / len(timed),
            "errors": sum(m.classification in ("mistake", "blunder") for m in timed),
            **{flag: flags.get(flag, 0) for flag in insights.TIME_FLAGS},
        }


class Tablebase(Protocol):
    def get_wdl(self, board: chess.Board) -> int | None: ...

    def get_dtz(self, board: chess.Board) -> int | None: ...


DEFAULT_DEPTH = 16
DEFAULT_MAX_TIME = 15.0


def available_cpus() -> int:
    """Return the number of CPU cores this process may run on."""
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:  # not available on macOS and Windows
        return os.cpu_count() or 1


def default_threads(workers: int = 1) -> int:
    """Engine threads per worker that leave one core free for the system."""
    return max(1, (available_cpus() - 1) // max(1, workers))


def make_limit(
    depth: int | None = DEFAULT_DEPTH,
    time: float | None = None,
    max_time: float | None = DEFAULT_MAX_TIME,
) -> chess.engine.Limit:
    """Build the per-position search limit.

    A fixed depth gives the same quality on slow and fast hardware; depth 16
    is enough to find most tactical blunders. ``max_time`` stops a search that
    takes unusually long before reaching that depth. Without a depth, the
    engine searches for ``time`` seconds per position.
    """
    if depth:
        return chess.engine.Limit(depth=depth, time=max_time or None)
    return chess.engine.Limit(time=time or 0.5)


def describe_limit(limit: chess.engine.Limit) -> str:
    """Describe a search limit, e.g. ``depth 16 (max 15s)``."""
    if limit.depth:
        cap = f" (max {limit.time:g}s)" if limit.time else ""
        return f"depth {limit.depth}{cap}"
    return f"{limit.time:g}s per move"


def open_engine(
    path: str,
    threads: int | None = None,
    hash_mb: int | None = None,
    syzygy_path: str | None = None,
    nice: int = 0,
) -> chess.engine.SimpleEngine:
    """Start a UCI engine and configure it.

    ``nice`` lowers the engine's CPU priority (0-19), so the rest of the system
    stays responsive while it searches.
    """
    command = [path]
    if nice and shutil.which("nice"):
        command = ["nice", "-n", str(nice), path]
    engine = chess.engine.SimpleEngine.popen_uci(command)
    configure_engine(engine, threads=threads, hash_mb=hash_mb, syzygy_path=syzygy_path)
    return engine


def engine_major_version(engine: chess.engine.SimpleEngine) -> int | None:
    """Return the major version from an id like ``Stockfish 17.1``."""
    name = engine.id.get("name", "")
    for part in name.split():
        major = part.split(".")[0]
        if major.isdigit():
            return int(major)
    return None


def configure_engine(
    engine: chess.engine.SimpleEngine,
    threads: int | None = None,
    hash_mb: int | None = None,
    syzygy_path: str | None = None,
) -> None:
    """Set the engine's threads, hash size and tablebase path, if supported.

    With ``syzygy_path`` the engine also uses the tablebases inside its
    search, which helps in positions just above the tablebase piece count.
    """
    wanted = {"Threads": threads, "Hash": hash_mb, "SyzygyPath": syzygy_path}
    options = {k: v for k, v in wanted.items() if v and k in engine.options}
    if options:
        engine.configure(options)


def open_tablebase(path: str) -> chess.syzygy.Tablebase:
    """Open the Syzygy tables (``.rtbw``/``.rtbz``) in a directory."""
    return chess.syzygy.open_tablebase(path)


@dataclass
class Evaluation:
    """Score (White's point of view) and best move of a position."""

    score: int
    best: chess.Move | None
    source: str


def _terminal_score(board: chess.Board) -> int:
    if board.is_checkmate():
        return -MATE_SCORE if board.turn == chess.WHITE else MATE_SCORE
    return 0


def _wdl_score(wdl: int, turn: chess.Color) -> int:
    """Map a side-to-move WDL value to White-POV centipawns.

    Cursed wins and blessed losses (``±1``) are draws under the fifty-move
    rule, so they score 0.
    """
    score = TABLEBASE_WIN if wdl == 2 else -TABLEBASE_WIN if wdl == -2 else 0
    return score if turn == chess.WHITE else -score


def tablebase_best_move(tablebase: Tablebase, board: chess.Board) -> chess.Move | None:
    """Return the tablebase's best move, or ``None`` if a probe failed.

    Moves are ranked by their result, then (when DTZ tables are present) by
    progress: the winning side prefers zeroing moves and short distances to
    zeroing, the losing side long ones.
    """
    best, best_key = None, None
    for move in list(board.legal_moves):
        zeroing = board.is_zeroing(move)
        board.push(move)
        try:
            if board.is_checkmate():
                key: tuple = (3,)
            elif board.is_game_over():  # stalemate or insufficient material
                key = (0,)
            else:
                wdl = tablebase.get_wdl(board)
                if wdl is None:
                    return None
                result = -wdl
                dtz = tablebase.get_dtz(board)
                distance = abs(dtz) if dtz is not None else 0
                if result > 0:
                    key = (result, zeroing, -distance)
                elif result < 0:
                    key = (result, distance)
                else:
                    key = (result,)
        finally:
            board.pop()
        if best_key is None or key > best_key:
            best, best_key = move, key
    return best


def _probe(tablebase: Tablebase | None, board: chess.Board) -> Evaluation | None:
    if tablebase is None:
        return None
    wdl = tablebase.get_wdl(board)
    if wdl is None:
        return None
    best = tablebase_best_move(tablebase, board)
    if best is None:
        return None
    return Evaluation(_wdl_score(wdl, board.turn), best, TABLEBASE)


def _search(
    engine: chess.engine.SimpleEngine, board: chess.Board, limit: chess.engine.Limit
) -> Evaluation:
    info = engine.analyse(board, limit)
    score = info["score"].white().score(mate_score=MATE_SCORE)
    pv = info.get("pv") or [None]
    return Evaluation(score, pv[0], ENGINE)


def _cap(cp: int) -> int:
    return max(-EVAL_CAP, min(EVAL_CAP, cp))


def analyze_game(
    game: chess.pgn.Game,
    engine: chess.engine.SimpleEngine,
    limit: chess.engine.Limit | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    book_plies: Collection[int] = (),
    tablebase: Tablebase | None = None,
) -> GameAnalysis:
    """Evaluate every move of a game, searching as few positions as possible.

    Positions are evaluated once each, from the end of the game backwards.
    The evaluation before a move gives the best move and its score; the
    evaluation after it gives the score of the move that was played.

    Parameters
    ----------
    game : chess.pgn.Game
        The game to analyse.
    engine : chess.engine.SimpleEngine
        An open UCI engine.
    limit : chess.engine.Limit, optional
        Search limit per position. Defaults to 0.5 seconds.
    on_progress : callable, optional
        Called as ``on_progress(done, total)`` after each position that needed
        evaluating.
    book_plies : collection of int, optional
        Plies (1-based) whose move is opening theory. They are classified as
        ``book`` and need no evaluation; see
        :func:`chess_analyzer.openings.book_plies`.
    tablebase : Tablebase, optional
        Syzygy tablebases. Positions they cover are not searched.

    Returns
    -------
    GameAnalysis
        One entry per half-move.
    """
    limit = limit or chess.engine.Limit(time=0.5)
    moves = list(game.mainline_moves())
    boards = [game.board()]
    for move in moves:
        # Keep the move stack: the engine needs it to recognise repetitions.
        board = boards[-1].copy()
        board.push(move)
        boards.append(board)
    n = len(moves)
    book = set(book_plies)

    # Position i is needed before move i + 1 and after move i, unless those
    # moves are book moves.
    needed = [
        (i < n and i + 1 not in book) or (i > 0 and i not in book) for i in range(n + 1)
    ]
    total = sum(needed)
    evals: list[Evaluation | None] = [None] * (n + 1)
    done = 0
    for i in reversed(range(n + 1)):
        if not needed[i]:
            continue
        board = boards[i]
        if board.is_game_over():
            evals[i] = Evaluation(_terminal_score(board), None, TERMINAL)
        elif (probed := _probe(tablebase, board)) is not None:
            evals[i] = probed
        elif i < n and evals[i + 1] is not None and board.legal_moves.count() == 1:
            evals[i] = Evaluation(evals[i + 1].score, moves[i], FORCED)
        else:
            evals[i] = _search(engine, board, limit)
        done += 1
        if on_progress is not None:
            on_progress(done, total)

    times, base = insights.move_times(game)
    analysis = GameAnalysis(sources=Counter(e.source if e else BOOK for e in evals))
    for ply, move in enumerate(moves, start=1):
        before, after = evals[ply - 1], evals[ply]
        board = boards[ply - 1]
        color = board.turn
        sign = 1 if color == chess.WHITE else -1
        if ply in book or before is None or after is None:
            classification, cp_loss, best = "book", 0, None
        elif before.source == FORCED:
            classification, cp_loss, best = "forced", 0, move
        else:
            best = before.best
            # Playing the evaluator's own choice costs nothing; any difference
            # between two searches is just noise.
            cp_loss = (
                0
                if move == best
                else max(0, sign * (_cap(before.score) - _cap(after.score)))
            )
            classification = classify(cp_loss, move == best)
        reply = after.best if after else None
        category = ""
        if classification in ("mistake", "blunder"):
            category = insights.categorize(
                board,
                move,
                best,
                reply,
                sign * before.score,
                sign * after.score,
                MATE_THRESHOLD,
            )
        time = times[ply - 1]
        analysis.moves.append(
            MoveAnalysis(
                ply=ply,
                color=color,
                san=board.san(move),
                best_san=board.san(best) if best else "",
                eval_before=before.score if before else None,
                eval_after=after.score if after else None,
                cp_loss=cp_loss,
                classification=classification,
                fen_before=board.fen(),
                uci=move.uci(),
                best_uci=best.uci() if best else "",
                fen_after=boards[ply].fen(),
                source=after.source if after else BOOK,
                source_before=before.source if before else BOOK,
                reply_uci=reply.uci() if reply else "",
                reply_san=boards[ply].san(reply) if reply else "",
                phase=insights.game_phase(board),
                category=category,
                clock=time.clock if time else None,
                time_spent=time.spent if time else None,
                time_flag=insights.time_flag(
                    classification, time, base, insights.is_obvious(board, move)
                ),
            )
        )
    return analysis
