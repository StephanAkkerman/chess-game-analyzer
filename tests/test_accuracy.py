from itertools import pairwise

import pytest

from chess_analyzer.accuracy import (
    estimated_rating,
    move_accuracy,
    side_accuracy,
    side_scores,
    win_percent,
)
from chess_analyzer.serialize import with_scores


def moves_from(evals):
    """Moves alternating white and black through the evaluations ``evals``."""
    return [
        {
            "color": "white" if i % 2 == 0 else "black",
            "eval_before": before,
            "eval_after": after,
        }
        for i, (before, after) in enumerate(pairwise(evals))
    ]


def test_win_percent():
    assert win_percent(0) == pytest.approx(50)
    assert win_percent(300) > 70
    assert win_percent(-300) < 30
    # Capped, so a mate counts as a won position.
    assert win_percent(9_998) == win_percent(1_000)


def test_move_accuracy():
    assert move_accuracy(50, 50) == 100
    assert move_accuracy(40, 60) == 100
    assert move_accuracy(80, 20) < 10
    assert 0 <= move_accuracy(100, 0) < 1


def test_accuracy_of_perfect_and_bad_play():
    steady = moves_from([20, 20] * 15)
    assert side_accuracy(steady, "white") == pytest.approx(100)

    # Black throws away a piece every few moves.
    evals = [20, 20, 20, 20, 300, 300, 300, 300, 650, 650, 650, 650, 1000]
    moves = moves_from(evals)
    assert side_accuracy(moves, "white") > 95
    assert side_accuracy(moves, "black") < 60


def test_book_moves_are_left_out():
    moves = [{"color": "white", "eval_before": None, "eval_after": None}]
    assert side_accuracy(moves, "white") is None
    moves += moves_from([None, 20, 20])[1:]
    assert side_accuracy(moves, "black") == pytest.approx(100)
    assert side_accuracy(moves, "white") is None


def test_estimated_rating():
    assert estimated_rating(None, 30) is None
    assert estimated_rating(90, 5) is None
    assert estimated_rating(30, 30) == 250
    assert estimated_rating(99, 30) == 3000
    assert estimated_rating(80, 30) % 50 == 0
    assert (
        estimated_rating(70, 30) < estimated_rating(80, 30) < estimated_rating(90, 30)
    )


def test_side_scores_and_old_results():
    moves = moves_from([20, 20] * 15)
    scores = side_scores(moves, "black")
    assert scores == {"accuracy": 100.0, "rating": 3000}

    old = {"moves": moves, "summary": {"white": {"acpl": 0}, "black": {"acpl": 0}}}
    new = with_scores(old)
    assert new["summary"]["white"]["acpl"] == 0
    assert new["summary"]["white"]["accuracy"] == 100.0
    assert "accuracy" not in old["summary"]["white"]
    assert with_scores(new) is new
