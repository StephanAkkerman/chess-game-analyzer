"""Evaluate games move by move, using the engine only where it is needed.

Each position is evaluated by the cheapest source that can answer it:

1. Opening book moves are not evaluated at all.
2. Positions with few pieces are looked up in Syzygy endgame tablebases.
3. A position with a single legal move takes the evaluation of the next one.
4. Everything else is searched by a UCI engine such as Stockfish.

Roughly equal positions are then searched again for the second-best move,
which shows the critical moments of a game: positions where only one move
keeps the balance.
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

# A critical moment is a position where the side to move stands roughly equal
# (within CRITICAL_EQUAL) with the best move, but the second-best move leaves
# it at least CRITICAL_DROP behind. At most CRITICAL_PER_GAME are kept per
# side, those with the largest gap between the two moves.
CRITICAL_EQUAL = 100
CRITICAL_DROP = 200
CRITICAL_PER_GAME = 3

# A brilliant move gives up at least this much material (in pawns), from a
# position that was not already won by BRILLIANT_WINNING or more. Brilliant
# moves and misses leave the side that moved at HOLDING or better.
BRILLIANT_MATERIAL = 2
BRILLIANT_WINNING = 500
HOLDING = -50

# The classifications, from best to worst, as on Chess.com:
#
# - brilliant: a sound sacrifice (see :func:`is_brilliant`);
# - great: the only move that kept the balance in a critical moment;
# - miss: a mistake or blunder that let a winning tactic or a mate slip,
#   without making the position bad;
# - the others by centipawn loss, see :func:`classify`.
CLASSIFICATIONS = (
    "book",
    "forced",
    "brilliant",
    "great",
    "best",
    "good",
    "inaccuracy",
    "mistake",
    "miss",
    "blunder",
)
# Moves that cost a lot: they are categorised and listed as the worst moves.
ERRORS = ("mistake", "miss", "blunder")
# Moves that found the engine's best move (or one as good).
FOUND = ("brilliant", "great", "best")
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

    ``second_san`` and ``second_eval`` give the engine's second-best move in
    the position before the move and its evaluation, when it was searched.
    ``critical`` marks a critical moment: see :func:`is_critical`.

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
    second_san: str = ""
    second_eval: int | None = None
    critical: bool = False

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
        """Return the ``n`` costliest errors (see :data:`ERRORS`)."""
        bad = [m for m in self.for_color(color) if m.classification in ERRORS]
        return sorted(bad, key=lambda m: m.cp_loss, reverse=True)[:n]

    def critical_moments(self, color: chess.Color) -> list[MoveAnalysis]:
        """Return the critical moments where ``color`` was to move, in order."""
        return [m for m in self.for_color(color) if m.critical]

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
            "errors": sum(m.classification in ERRORS for m in timed),
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
    """Score (White's point of view) and best move of a position.

    ``second`` and ``second_score`` are the second-best move and its score,
    when the engine was asked for them.
    """

    score: int
    best: chess.Move | None
    source: str
    second: chess.Move | None = None
    second_score: int | None = None


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
    engine: chess.engine.SimpleEngine,
    board: chess.Board,
    limit: chess.engine.Limit,
    root_moves: list[chess.Move] | None = None,
) -> Evaluation:
    """Search ``board``, only among ``root_moves`` if given."""
    if root_moves is None:
        info = engine.analyse(board, limit)
    else:
        info = engine.analyse(board, limit, root_moves=root_moves)
    score = info["score"].white().score(mate_score=MATE_SCORE)
    pv = info.get("pv") or [None]
    return Evaluation(score, pv[0], ENGINE)


def _search_second(
    engine: chess.engine.SimpleEngine,
    board: chess.Board,
    limit: chess.engine.Limit,
    evaluation: Evaluation,
) -> None:
    """Find the second-best move by searching every move but the best one.

    That is cheaper than asking the engine for two lines at once.
    """
    others = [m for m in board.legal_moves if m != evaluation.best]
    if not others:
        return
    second = _search(engine, board, limit, root_moves=others)
    if second.best is not None:
        evaluation.second, evaluation.second_score = second.best, second.score


def _may_be_critical(
    board: chess.Board,
    move: chess.Move,
    evals: list[Evaluation | None],
    i: int,
) -> bool:
    """Return whether position ``i`` could be a critical moment.

    Only searched, roughly equal positions qualify. When the game move was
    not the best one but kept the side to move above ``-CRITICAL_DROP``, the
    second-best move is at least as good, so the position is not critical.
    """
    before, after = evals[i], evals[i + 1]
    if before is None or after is None or before.source != ENGINE:
        return False
    if before.best is None or insights.is_obvious(board, before.best):
        return False
    sign = 1 if board.turn == chess.WHITE else -1
    if abs(_cap(before.score)) > CRITICAL_EQUAL:
        return False
    return move == before.best or sign * _cap(after.score) <= -CRITICAL_DROP


def is_critical(
    board: chess.Board, before: Evaluation, best_score: int, second_score: int
) -> bool:
    """Return whether only one move keeps the balance in ``board``.

    ``best_score`` and ``second_score`` are the evaluations of the best and
    second-best move from the point of view of the side to move. The best move
    must keep the position roughly equal, and the second-best must lose at
    least :data:`CRITICAL_DROP`. Plain recaptures are left out: taking back a
    piece is usually the only good move, but it is not a decision.
    """
    if before.best is None or before.second is None:
        return False
    if abs(best_score) > CRITICAL_EQUAL or second_score > -CRITICAL_DROP:
        return False
    return not insights.is_obvious(board, before.best)


def is_brilliant(board: chess.Board, move: chess.Move, before: int, after: int) -> bool:
    """Return whether ``move`` was a sound sacrifice.

    ``before`` and ``after`` are the evaluations before and after the move,
    from the point of view of the side that moved. The move must give up a
    piece (see :func:`chess_analyzer.insights.material_offered`), from a
    position that was not already easily won, and still keep the balance.
    """
    if before >= BRILLIANT_WINNING or after < HOLDING:
        return False
    if insights.is_obvious(board, move):
        return False
    return insights.material_offered(board, move) >= BRILLIANT_MATERIAL


def is_miss(category: str, after: int) -> bool:
    """Return whether an error only let a win slip, as Chess.com's "miss".

    That is a missed mate or tactic after which the side that moved, from
    whose point of view ``after`` is, still stands at least about equal.
    """
    return category in (insights.MISSED_MATE, insights.MISSED_TACTIC) and (
        after >= HOLDING
    )


def _cap(cp: int) -> int:
    return max(-EVAL_CAP, min(EVAL_CAP, cp))


def analyze_game(
    game: chess.pgn.Game,
    engine: chess.engine.SimpleEngine,
    limit: chess.engine.Limit | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    book_plies: Collection[int] = (),
    tablebase: Tablebase | None = None,
    critical: bool = True,
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
    critical : bool, optional
        Find the game's critical moments, by searching the roughly equal
        positions again for the second-best move. Costs some extra engine
        time.

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

    if critical:
        # Search the positions that may be critical again, for the
        # second-best move.
        candidates = [
            i
            for i in range(n)
            if i + 1 not in book and _may_be_critical(boards[i], moves[i], evals, i)
        ]
        total += len(candidates)
        for i in reversed(candidates):
            _search_second(engine, boards[i], limit, evals[i])
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
        second = before.second if before and classification != "book" else None
        second_score = before.second_score if second else None
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
            if is_miss(category, sign * _cap(after.score)):
                classification = "miss"
        elif classification in ("best", "good") and is_brilliant(
            board, move, sign * _cap(before.score), sign * _cap(after.score)
        ):
            classification = "brilliant"
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
                second_san=board.san(second) if second else "",
                second_eval=second_score,
            )
        )
        if second is not None and is_critical(
            board, before, sign * _cap(before.score), sign * _cap(second_score)
        ):
            analysis.moves[-1].critical = True
            if classification == "best":
                analysis.moves[-1].classification = "great"
    _keep_most_critical(analysis)
    return analysis


def _keep_most_critical(analysis: GameAnalysis) -> None:
    """Keep the :data:`CRITICAL_PER_GAME` sharpest critical moments per side."""
    for color in (chess.WHITE, chess.BLACK):
        moments = analysis.critical_moments(color)

        def gap(m: MoveAnalysis) -> int:
            return abs(_cap(m.eval_before) - _cap(m.second_eval))

        for m in sorted(moments, key=gap, reverse=True)[CRITICAL_PER_GAME:]:
            m.critical = False
