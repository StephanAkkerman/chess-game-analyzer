import chess
import chess.engine
import pytest

from chess_analyzer.engine import (
    MATE_SCORE,
    TABLEBASE_WIN,
    analyze_game,
    classify,
    default_threads,
    describe_limit,
    find_engine,
    make_limit,
    tablebase_best_move,
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
        score, best = self.evals[len(board.move_stack)]  # keyed by ply
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


def test_book_moves_are_not_searched(scholars_mate_pgn):
    game = read_games(scholars_mate_pgn)[0]
    engine = FakeEngine(EVALS)
    analysis = analyze_game(game, engine, book_plies={1, 2, 3, 4, 5})

    # Only the position before the first non-book move and the one after it.
    assert engine.calls == 2
    assert [m.classification for m in analysis.moves] == ["book"] * 5 + [
        "blunder",
        "best",
    ]
    assert analysis.moves[0].eval_before is None
    assert analysis.moves[4].eval_after == -20
    assert analysis.sources == {"book": 5, "engine": 2, "terminal": 1}
    # Book and forced moves do not count towards the average loss.
    assert analysis.average_cp_loss(chess.BLACK) == 1020


# Black's king on a8 is in check from the rook and has a single move, Kb8.
FORCED = '[FEN "k7/8/1K6/8/8/8/8/R7 b - - 0 1"]\n\n1... Kb8 2. Rh1 Kc8 *'


def test_forced_moves_reuse_the_next_evaluation():
    game = read_games(FORCED)[0]
    engine = FakeEngine([(0, "a8b8"), (900, "a1a7"), (950, "b8c8"), (950, "h1h8")])
    analysis = analyze_game(game, engine)

    assert engine.calls == 3
    kb8 = analysis.moves[0]
    assert (kb8.classification, kb8.eval_before, kb8.cp_loss) == ("forced", 900, 0)
    assert analysis.sources["forced"] == 1


class FakeTablebase:
    """White wins while it keeps its rook; everything else is drawn."""

    def get_wdl(self, board):
        if chess.popcount(board.occupied) > 4 or board.is_game_over():
            return None
        rooks = board.pieces(chess.ROOK, chess.WHITE)
        hanging = board.turn == chess.BLACK and any(
            board.is_capture(m) and m.to_square in rooks for m in board.legal_moves
        )
        if rooks and not hanging:
            return 2 if board.turn == chess.WHITE else -2
        return 0

    def get_dtz(self, board):
        wdl = self.get_wdl(board)
        return None if wdl is None else 10 * wdl


def test_tablebase_positions_are_not_searched():
    game = read_games(FORCED)[0]
    engine = FakeEngine([])
    analysis = analyze_game(game, engine, tablebase=FakeTablebase())

    assert engine.calls == 0
    assert analysis.sources == {"tablebase": 4}
    assert [m.eval_after for m in analysis.moves] == [TABLEBASE_WIN] * 3
    assert all(m.cp_loss == 0 for m in analysis.moves)
    assert analysis.moves[0].source == "tablebase"


def test_tablebase_best_move_keeps_the_win():
    # Rxb7?? hangs the rook to the king; every other rook move keeps it.
    board = chess.Board("k7/1p6/8/8/8/8/8/1R4K1 w - - 0 1")
    best = tablebase_best_move(FakeTablebase(), board)
    assert best is not None
    assert best != chess.Move.from_uci("b1b7")


def test_tablebase_blunder_is_found():
    game = read_games('[FEN "k7/8/8/8/8/8/8/1R4K1 w - - 0 1"]\n\n1. Rb7 Kxb7 *')[0]
    analysis = analyze_game(game, FakeEngine([]), tablebase=FakeTablebase())
    rb7 = analysis.moves[0]
    assert rb7.classification == "blunder"
    assert (rb7.eval_before, rb7.eval_after) == (TABLEBASE_WIN, 0)
    assert (rb7.source_before, rb7.source) == ("tablebase", "tablebase")


def test_make_limit():
    assert make_limit() == chess.engine.Limit(depth=16, time=15)
    assert make_limit(depth=20, max_time=None) == chess.engine.Limit(depth=20)
    assert make_limit(depth=0, time=0.3) == chess.engine.Limit(time=0.3)
    assert describe_limit(make_limit()) == "depth 16 (max 15s)"
    assert describe_limit(make_limit(depth=None, time=0.3)) == "0.3s per move"


def test_default_threads_leaves_a_core_free(monkeypatch):
    monkeypatch.setattr("chess_analyzer.engine.available_cpus", lambda: 4)
    assert default_threads() == 3
    assert default_threads(workers=2) == 1
    monkeypatch.setattr("chess_analyzer.engine.available_cpus", lambda: 1)
    assert default_threads() == 1
