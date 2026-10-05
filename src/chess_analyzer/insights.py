"""Explain moves beyond their centipawn loss.

For each move this module works out:

- the game phase it was played in (opening, middlegame or endgame);
- for mistakes and blunders, what kind of error it was, such as hanging a
  piece or missing a tactic;
- from the PGN clock comments, how long the move took and whether that
  points to a time-management problem.

The error categories are heuristics based on the engine's best move and the
opponent's best reply, not on a full understanding of the position.
"""

from __future__ import annotations

from dataclasses import dataclass

import chess
import chess.pgn

PIECE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}

OPENING, MIDDLEGAME, ENDGAME = "opening", "middlegame", "endgame"
PHASES = (OPENING, MIDDLEGAME, ENDGAME)
# Positions with at most this many queens, rooks, bishops and knights are
# endgames (the definition Lichess uses).
ENDGAME_PIECES = 6
# The opening ends after this move, or earlier once pieces are traded off.
OPENING_MOVES = 10
OPENING_PIECES = 10

ALLOWED_MATE = "allowed_mate"
MISSED_MATE = "missed_mate"
HUNG_PIECE = "hung_piece"
REFUTED_ATTACK = "refuted_attack"
ALLOWED_TACTIC = "allowed_tactic"
CONVERSION = "conversion"
MISSED_TACTIC = "missed_tactic"
POSITIONAL = "positional"
CATEGORIES = (
    ALLOWED_MATE,
    MISSED_MATE,
    HUNG_PIECE,
    REFUTED_ATTACK,
    ALLOWED_TACTIC,
    CONVERSION,
    MISSED_TACTIC,
    POSITIONAL,
)
CATEGORY_LABELS = {
    ALLOWED_MATE: "allowed mate",
    MISSED_MATE: "missed mate",
    HUNG_PIECE: "hung a piece",
    REFUTED_ATTACK: "unsound attack",
    ALLOWED_TACTIC: "allowed a tactic",
    CONVERSION: "spoiled a won endgame",
    MISSED_TACTIC: "missed tactic",
    POSITIONAL: "positional",
}
# Evaluations, from the mover's point of view, that count as winning and as
# no longer winning when judging conversion mistakes.
WINNING = 300
NOT_WINNING = 100

IMPULSIVE, TIME_TROUBLE, WASTED_TIME = "impulsive", "time_trouble", "wasted_time"
TIME_FLAGS = (IMPULSIVE, TIME_TROUBLE, WASTED_TIME)
# A mistake played faster than this is impulsive.
IMPULSIVE_SECONDS = 3.0
# Less than this share of the starting clock left is time trouble.
TIME_TROUBLE_SHARE = 0.1
# Spending this share of the starting clock (at most two minutes) on an
# obvious move wastes time.
WASTED_TIME_SHARE = 0.1
WASTED_TIME_SECONDS = 120.0


def game_phase(board: chess.Board) -> str:
    """Return the phase of the game in ``board``."""
    pieces = chess.popcount(board.knights | board.bishops | board.rooks | board.queens)
    if pieces <= ENDGAME_PIECES:
        return ENDGAME
    if board.fullmove_number <= OPENING_MOVES and pieces > OPENING_PIECES:
        return OPENING
    return MIDDLEGAME


def wins_material(board: chess.Board, capture: chess.Move) -> bool:
    """Return whether ``capture`` wins a piece (not just a pawn) in ``board``.

    That is the case when the captured piece is undefended or worth more than
    the piece that takes it.
    """
    victim = board.piece_at(capture.to_square)
    attacker = board.piece_at(capture.from_square)
    if victim is None or attacker is None or victim.piece_type == chess.PAWN:
        return False
    if PIECE_VALUES[victim.piece_type] > PIECE_VALUES[attacker.piece_type]:
        return True
    return not board.is_attacked_by(victim.color, capture.to_square)


def is_forcing(board: chess.Board, move: chess.Move) -> bool:
    return board.is_capture(move) or board.gives_check(move)


def categorize(
    board: chess.Board,
    move: chess.Move,
    best: chess.Move | None,
    reply: chess.Move | None,
    before: int,
    after: int,
    mate: int,
) -> str:
    """Say what kind of error ``move`` was.

    Parameters
    ----------
    board : chess.Board
        The position before the move.
    move : chess.Move
        The move that was played.
    best : chess.Move or None
        The engine's best move in ``board``.
    reply : chess.Move or None
        The opponent's best reply after ``move``.
    before, after : int
        Evaluations before and after the move, in centipawns from the point
        of view of the side that moved.
    mate : int
        Evaluations at or beyond this value are forced mates.

    Returns
    -------
    str
        One of :data:`CATEGORIES`.
    """
    if after <= -mate < before:
        return ALLOWED_MATE
    if before >= mate > after:
        return MISSED_MATE
    position = board.copy(stack=False)
    position.push(move)
    if (
        reply is not None
        and reply in position.legal_moves
        and position.is_capture(reply)
        and wins_material(position, reply)
    ):
        # The piece that just moved, after a capture or check, was taken.
        if reply.to_square == move.to_square and is_forcing(board, move):
            return REFUTED_ATTACK
        return HUNG_PIECE
    if game_phase(board) == ENDGAME and before >= WINNING and after < NOT_WINNING:
        return CONVERSION
    if (
        reply is not None
        and reply in position.legal_moves
        and is_forcing(position, reply)
    ):
        return ALLOWED_TACTIC
    if (
        best is not None
        and is_forcing(board, best)
        # Taking the same piece in another way is not a missed tactic.
        and not (board.is_capture(move) and move.to_square == best.to_square)
    ):
        return MISSED_TACTIC
    return POSITIONAL


def parse_time_control(time_control: str | None) -> tuple[float, float] | None:
    """Parse a PGN ``TimeControl`` such as ``600+5`` into base and increment.

    Returns ``None`` for daily games (``1/86400``) and unknown time controls.
    """
    if not time_control or "/" in time_control:
        return None
    base, _, increment = time_control.partition("+")
    try:
        return float(base), float(increment or 0)
    except ValueError:
        return None


@dataclass
class MoveTime:
    """Clock data of one move, in seconds."""

    clock: float
    spent: float
    clock_before: float


def move_times(game: chess.pgn.Game) -> tuple[list[MoveTime | None], float | None]:
    """Read the clock comments (``[%clk 0:02:59]``) of a game.

    Returns
    -------
    times : list of MoveTime or None
        One entry per half-move, ``None`` where the clock is unknown.
    base : float or None
        The starting clock, or ``None`` if the time control is unknown.
    """
    control = parse_time_control(game.headers.get("TimeControl"))
    nodes = list(game.mainline())
    if control is None:
        return [None] * len(nodes), None
    base, increment = control
    previous = {chess.WHITE: base, chess.BLACK: base}
    times: list[MoveTime | None] = []
    for node in nodes:
        color = not node.board().turn
        clock = node.clock()
        if clock is None:
            times.append(None)
            continue
        before = previous[color]
        spent = max(0.0, before - clock + increment)
        times.append(MoveTime(clock=clock, spent=spent, clock_before=before))
        previous[color] = clock
    return times, base


def is_obvious(board: chess.Board, move: chess.Move) -> bool:
    """Return whether ``move`` is the only legal move or a plain recapture."""
    if board.legal_moves.count() == 1:
        return True
    if not board.move_stack or not board.is_capture(move):
        return False
    last = board.peek()
    board.pop()
    try:
        was_capture = board.is_capture(last)
    finally:
        board.push(last)
    return was_capture and last.to_square == move.to_square


def time_flag(
    classification: str,
    time: MoveTime | None,
    base: float | None,
    obvious: bool,
) -> str:
    """Flag a move whose timing points to a time-management problem.

    - ``time_trouble``: a mistake or blunder with less than 10% of the
      starting clock left;
    - ``impulsive``: any other mistake or blunder played in under 3 seconds;
    - ``wasted_time``: a long think (10% of the starting clock, at most two
      minutes) on an obvious move that was played correctly.

    Returns an empty string when nothing stands out.
    """
    if time is None or not base:
        return ""
    if classification in ("mistake", "blunder"):
        if time.clock_before < TIME_TROUBLE_SHARE * base:
            return TIME_TROUBLE
        if time.spent < IMPULSIVE_SECONDS:
            return IMPULSIVE
        return ""
    threshold = min(WASTED_TIME_SECONDS, WASTED_TIME_SHARE * base)
    if (
        obvious
        and classification in ("forced", "best", "good")
        and (time.spent >= threshold)
    ):
        return WASTED_TIME
    return ""
