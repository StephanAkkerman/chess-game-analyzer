"""Accuracy in percent and an estimated rating for one side of a game.

Centipawn loss punishes a slip in a won position as hard as one in a
balanced position, though only the second changes the result. Accuracy
counts what each move did to the winning chances instead, as Lichess does
(https://lichess.org/page/accuracy):

1. Every evaluation becomes a win percentage for the side that moved.
2. A move's accuracy follows from how much of it the move gave away: 100%
   for a move that kept it, falling towards 0% as the drop grows.
3. The game's accuracy averages the moves twice, once weighted by how
   volatile the position was around each move and once harmonically, so a
   few bad moves weigh in more than many easy ones, and takes the mean of
   the two.

The estimated rating maps the accuracy to the strength of a player who
typically plays that accurately. It is a rough guide for one game, not a
measured rating: one sharp game can read 300 points above or below one's
real strength.

Both work on serialized moves (see :mod:`chess_analyzer.serialize`), so
analyses stored before accuracy existed get it too.
"""

from __future__ import annotations

import math
from itertools import pairwise
from statistics import pstdev

# Evaluations beyond this are as good as won.
EVAL_CAP = 1_000
# Fewer moves with an evaluation than this give no estimated rating.
RATING_MIN_MOVES = 10
# Accuracy and the rating of a player who typically plays that accurately,
# interpolated in between. Fitted roughly to Lichess and Chess.com games.
RATING_POINTS = (
    (40.0, 250),
    (55.0, 600),
    (65.0, 900),
    (72.0, 1200),
    (78.0, 1500),
    (83.0, 1800),
    (87.0, 2100),
    (91.0, 2400),
    (94.0, 2700),
    (97.0, 3000),
)


def win_percent(cp: int) -> float:
    """Winning chances in percent for an evaluation in centipawns."""
    cp = max(-EVAL_CAP, min(EVAL_CAP, cp))
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


def move_accuracy(win_before: float, win_after: float) -> float:
    """Accuracy of a move from the mover's winning chances before and after."""
    drop = win_before - win_after
    raw = 103.1668100711649 * math.exp(-0.04354415386753951 * drop) - 3.166924740191411
    # Lichess adds a point for the engine's own uncertainty.
    return max(0.0, min(100.0, raw + 1))


def _evaluated(moves: list[dict]) -> list[dict]:
    return [
        m
        for m in moves
        if m.get("eval_before") is not None and m.get("eval_after") is not None
    ]


def _weights(moves: list[dict]) -> list[float]:
    """How volatile the position was around each move, from 0.5 to 12."""
    wins = [win_percent(moves[0]["eval_before"])]
    wins += [win_percent(m["eval_after"]) for m in moves]
    size = max(2, min(8, len(moves) // 10))
    windows = [wins[:size]] * min(size - 2, len(moves))
    windows += [wins[i : i + size] for i in range(len(wins) - size + 1)]
    return [max(0.5, min(12.0, pstdev(w))) for w in windows[: len(moves)]]


def side_accuracy(moves: list[dict], color: str) -> float | None:
    """Accuracy of ``color`` ("white" or "black") in percent.

    Moves without an evaluation, such as book moves, are left out. Returns
    ``None`` when ``color`` has no evaluated move.
    """
    evaluated = _evaluated(moves)
    if not evaluated:
        return None
    weights = _weights(evaluated)
    scores = []
    for move, weight in zip(evaluated, weights):
        if move["color"] != color:
            continue
        sign = 1 if color == "white" else -1
        before = win_percent(sign * move["eval_before"])
        after = win_percent(sign * move["eval_after"])
        scores.append((move_accuracy(before, after), weight))
    if not scores:
        return None
    weighted = sum(a * w for a, w in scores) / sum(w for _, w in scores)
    harmonic = len(scores) / sum(1 / max(1.0, a) for a, _ in scores)
    return (weighted + harmonic) / 2


def estimated_rating(accuracy: float | None, moves: int) -> int | None:
    """The rating a player with this accuracy typically has, to 50 points.

    ``moves`` is how many of the side's moves were evaluated: too few say
    little, and give ``None``.
    """
    if accuracy is None or moves < RATING_MIN_MOVES:
        return None
    points = RATING_POINTS
    if accuracy <= points[0][0]:
        rating = points[0][1]
    elif accuracy >= points[-1][0]:
        rating = points[-1][1]
    else:
        for (a0, r0), (a1, r1) in pairwise(points):
            if a0 <= accuracy <= a1:
                rating = r0 + (r1 - r0) * (accuracy - a0) / (a1 - a0)
                break
    return int(round(rating / 50) * 50)


def side_scores(moves: list[dict], color: str) -> dict:
    """``accuracy`` (to one decimal) and ``rating`` of ``color``, or ``None``s."""
    accuracy = side_accuracy(moves, color)
    count = sum(m["color"] == color for m in _evaluated(moves))
    return {
        "accuracy": None if accuracy is None else round(accuracy, 1),
        "rating": estimated_rating(accuracy, count),
    }
