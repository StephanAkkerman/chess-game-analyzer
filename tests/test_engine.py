import chess
import chess.engine
import pytest

from chess_analyzer.engine import (
    MATE_SCORE,
    analyze_game,
    classify,
    find_engine,
)
from chess_analyzer.parse import read_games

# White-POV score and engine best move for each ply of Scholar's Mate:
# 1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qxf7#
EVALS = [
    (30, "e2e4"),
    (30, "e7e5"),
    (30, "f1c4"),
    (30, "b8c6"),
    (30, "g1f3"),  # Qh5 is not best: an inaccuracy
    (-20, "g7g6"),  # Nf6 allows mate in one: a blunder
    (MATE_SCORE - 1, "h5f7"),
]


class FakeEngine:
    def __init__(self, evals):
        self.evals = evals
        self.calls = 0

    def analyse(self, board, limit):
        self.calls += 1
        score, best = self.evals[len(board.move_stack)]
        return {
            "score": chess.engine.PovScore(chess.engine.Cp(score), chess.WHITE),
            "pv": [chess.Move.from_uci(best)],
        }


@pytest.mark.parametrize(
    "loss, best, expected",
    [
        (0, True, "best"),
        (400, True, "best"),
        (49, False, "good"),
        (50, False, "inaccuracy"),
        (100, False, "mistake"),
        (299, False, "mistake"),
        (300, False, "blunder"),
    ],
)
def test_classify(loss, best, expected):
    assert classify(loss, best) == expected


def test_analyze_game_with_fake_engine(scholars_mate_pgn):
    game = read_games(scholars_mate_pgn)[0]
    engine = FakeEngine(EVALS)
    analysis = analyze_game(game, engine)

    # The final, checkmated position is scored without asking the engine.
    assert engine.calls == 7
    assert [m.classification for m in analysis.moves] == [
        "best",
        "best",
        "best",
        "best",
        "inaccuracy",
        "blunder",
        "best",
    ]
    qh5, nf6, qxf7 = analysis.moves[4:]
    assert (qh5.label, qh5.best_san, qh5.cp_loss) == ("3.Qh5", "Nf3", 50)
    assert (nf6.label, nf6.best_san) == ("3...Nf6", "g6")
    # Mate scores are capped, so the loss is 1000 + 20, not ~10000.
    assert nf6.cp_loss == 1020
    assert qxf7.eval_after == MATE_SCORE

    assert analysis.average_cp_loss(chess.WHITE) == pytest.approx(50 / 4)
    assert analysis.counts(chess.BLACK)["blunder"] == 1
    assert analysis.worst_moves(chess.BLACK) == [nf6]
    assert analysis.worst_moves(chess.WHITE) == []


@pytest.mark.skipif(find_engine() is None, reason="Stockfish is not installed")
def test_analyze_game_with_stockfish(scholars_mate_pgn):
    game = read_games(scholars_mate_pgn)[0]
    with chess.engine.SimpleEngine.popen_uci(find_engine()) as engine:
        analysis = analyze_game(game, engine, chess.engine.Limit(depth=12))
    nf6, qxf7 = analysis.moves[5:]
    assert nf6.classification == "blunder"
    assert qxf7.classification == "best"
