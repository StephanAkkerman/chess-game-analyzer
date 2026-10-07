import chess
import chess.engine
import pytest

from chess_analyzer.engine import (
    CRITICAL_PER_GAME,
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
    """Answer searches from a list of evaluations, keyed by ply.

    Each entry is ``(score, best)``, or ``(score, best, second_score,
    second)`` to also answer a search that leaves out the best move. Without
    a second line, another move scores as well as the best one.
    """

    def __init__(self, evals):
        self.evals = evals
        self.calls = 0
        # The plies searched without their best move.
        self.excluded = []

    @staticmethod
    def _info(score, move):
        return {
            "score": chess.engine.PovScore(chess.engine.Cp(score), chess.WHITE),
            "pv": [chess.Move.from_uci(move)],
        }

    def analyse(self, board, limit, root_moves=None):
        self.calls += 1
        ply = len(board.move_stack)
        entry = self.evals[ply]
        if root_moves is None:
            return self._info(*entry[:2])
        assert chess.Move.from_uci(entry[1]) not in root_moves
        self.excluded.append(ply)
        if len(entry) == 2:  # no second line given: another move holds
            return self._info(entry[0], root_moves[0].uci())
        return self._info(*entry[2:4])


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
    analysis = analyze_game(game, engine, critical=False)

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
    assert (nf6.category, nf6.reply_san, nf6.phase) == (
        "allowed_mate",
        "Qxf7#",
        "opening",
    )
    # Inaccuracies are not categorised, and the PGN has no clock times.
    assert qh5.category == ""
    assert qh5.time_spent is None
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
    analysis = analyze_game(game, engine, book_plies={1, 2, 3, 4, 5}, critical=False)

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


TIMED_PGN = """[TimeControl "180+0"]

1. e4 {[%clk 0:03:00]} 1... e5 {[%clk 0:02:59]} 2. Bc4 {[%clk 0:02:58]}
2... Nc6 {[%clk 0:02:50]} 3. Qh5 {[%clk 0:02:40]} 3... Nf6 {[%clk 0:02:49]}
4. Qxf7# {[%clk 0:02:39]} 1-0
"""


def test_clock_times_and_flags():
    game = read_games(TIMED_PGN)[0]
    analysis = analyze_game(game, FakeEngine(EVALS))
    nf6 = analysis.moves[5]
    assert (nf6.clock, nf6.time_spent, nf6.time_flag) == (169, 1, "impulsive")
    assert analysis.moves[4].time_spent == 18
    summary = analysis.time_summary(chess.BLACK)
    assert summary["moves"] == 3
    assert (summary["errors"], summary["impulsive"]) == (1, 1)
    assert analysis.categories(chess.BLACK) == {"allowed_mate": 1}
    assert analysis.phase_cp_loss(chess.WHITE) == {"opening": (50 / 4, 4)}


# Positions searched with two lines; see test_critical_moments.
CRITICAL_PGN = "1. e4 e5 2. Nf3 Nc6 3. Bc4 Bc5 4. Ng5 Nh6 5. Nxf7 Nxf7 6. Bxf7+ Kxf7 *"


CRITICAL_EVALS = [
    (30, "e2e4", 20, "d2d4"),
    (30, "e7e5", 50, "c7c5"),
    (30, "g1f3", 10, "b1c3"),
    # Only Nc6 holds: Black stays equal, the next move loses 3 pawns.
    (30, "b8c6", 330, "d7d6"),
    (30, "f1c4", -20, "f1b5"),
    # Black misses the only move: Nf6 holds, Bc5 is the game move.
    (30, "g8f6", 400, "f8c5"),
    (250, "f3g5", 100, "b1c3"),
    # Equal, but the second move is fine: not critical.
    (-50, "d7d5", -20, "g8h6"),
    # Only Nxf7 keeps White level.
    (0, "g5f7", -300, "c4f7"),
    # A plain recapture is left out even when only it holds.
    (0, "h6f7", 900, "d8f6"),
    (-80, "c4f7", -400, "d2d3"),
    (0, "e8f7", MATE_SCORE, "e8e7"),
    (0, "d2d3"),
]


def test_critical_moments():
    game = read_games(CRITICAL_PGN)[0]
    engine = FakeEngine(CRITICAL_EVALS)
    analysis = analyze_game(game, engine)

    # Every position is searched once, then the ones that may be critical
    # again without their best move. Those not equal (4.Ng5), where the game
    # move already shows a second move that holds (4...Nh6) and recaptures
    # are not.
    assert engine.calls == 13 + 7
    assert engine.excluded == [8, 5, 4, 3, 2, 1, 0]
    critical = [m.label for m in analysis.moves if m.critical]
    assert critical == ["2...Nc6", "3...Bc5", "5.Nxf7"]
    nc6, bc5 = analysis.critical_moments(chess.BLACK)
    # Finding the only move is a great move.
    assert (nc6.classification, nc6.second_san, nc6.second_eval) == ("great", "d6", 330)
    assert (bc5.best_san, bc5.second_san) == ("Nf6", "Bc5")
    # Bxf7+ and Kxf7 are recaptures: excluded.
    assert not analysis.moves[10].critical
    assert not analysis.moves[11].critical


def test_critical_moments_are_limited_per_game():
    game = read_games("1. Nf3 Nf6 2. Ng1 Ng8 3. Nf3 Nf6 4. Ng1 Ng8 5. Nf3 *")[0]
    evals = []
    for ply in range(10):
        knight = ["g1f3", "g8f6", "f3g1", "f6g8"][ply % 4]
        if ply % 2 == 0:
            # Every White move is critical, with a growing gap.
            evals.append((0, knight, -300 - 10 * ply, "b1c3"))
        else:
            evals.append((0, knight, 0, "b8c6"))
    analysis = analyze_game(game, FakeEngine(evals))
    white = analysis.critical_moments(chess.WHITE)
    assert len(white) == CRITICAL_PER_GAME
    assert [m.ply for m in white] == [5, 7, 9]
    assert analysis.critical_moments(chess.BLACK) == []


def test_critical_moments_can_be_skipped(scholars_mate_pgn):
    game = read_games(scholars_mate_pgn)[0]
    engine = FakeEngine(EVALS)
    analysis = analyze_game(game, engine, critical=False)
    assert engine.excluded == []
    assert not any(m.critical or m.second_san for m in analysis.moves)


def test_brilliant_sacrifice():
    game = read_games("1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6 4. Bxf7+ Kxf7 *")[0]
    best = ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "g8f6", "c4f7", "e8f7", "d2d4"]
    analysis = analyze_game(game, FakeEngine([(30, m) for m in best]), critical=False)
    bxf7, kxf7 = analysis.moves[6:]
    # The bishop is given up for a pawn and White still stands well.
    assert bxf7.classification == "brilliant"
    # Taking it back is the best move, but no sacrifice.
    assert kxf7.classification == "best"


def test_brilliant_needs_a_sound_position():
    game = read_games("1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6 4. Bxf7+ Kxf7 *")[0]
    best = ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "g8f6", "c4f7", "e8f7", "d2d4"]
    evals = [(30, m) for m in best]
    evals[7] = (-200, "e8f7")  # the sacrifice is the best of bad options
    analysis = analyze_game(game, FakeEngine(evals), critical=False)
    assert analysis.moves[6].classification != "brilliant"


def test_missed_mate_is_a_miss():
    game = read_games("1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qf3 d6 *")[0]
    evals = [
        (30, "e2e4"),
        (30, "e7e5"),
        (30, "f1c4"),
        (30, "b8c6"),
        (30, "d1h5"),
        (-20, "g7g6"),
        (MATE_SCORE - 1, "h5f7"),
        # After 4.Qf3 White is still better: Qxf7# was missed, nothing lost.
        (300, "d7d6"),
        (300, "d2d3"),
    ]
    analysis = analyze_game(game, FakeEngine(evals), critical=False)
    qf3 = analysis.moves[6]
    assert (qf3.classification, qf3.category) == ("miss", "missed_mate")
    assert analysis.worst_moves(chess.WHITE) == [qf3]
    assert analysis.counts(chess.WHITE)["miss"] == 1
