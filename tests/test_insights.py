import chess
import pytest

from chess_analyzer.insights import (
    ALLOWED_MATE,
    ALLOWED_TACTIC,
    CONVERSION,
    ENDGAME,
    HUNG_PIECE,
    IMPULSIVE,
    MIDDLEGAME,
    MISSED_MATE,
    MISSED_TACTIC,
    OPENING,
    POSITIONAL,
    REFUTED_ATTACK,
    TIME_TROUBLE,
    WASTED_TIME,
    MoveTime,
    categorize,
    game_phase,
    is_obvious,
    material_offered,
    move_times,
    parse_time_control,
    time_flag,
)
from chess_analyzer.parse import read_games

MATE = 9_500
M = chess.Move.from_uci


def test_game_phase():
    assert game_phase(chess.Board()) == OPENING
    middlegame = chess.Board()
    middlegame.fullmove_number = 15
    assert game_phase(middlegame) == MIDDLEGAME
    assert game_phase(chess.Board("4k3/8/8/8/8/8/8/R3K2R w - - 0 40")) == ENDGAME


def test_mates_are_recognised():
    board = chess.Board()
    assert categorize(board, M("e2e4"), None, None, 0, -MATE, MATE) == ALLOWED_MATE
    assert categorize(board, M("e2e4"), None, None, MATE, 300, MATE) == MISSED_MATE


def test_hung_piece():
    # Nd5?? puts the knight where the e6 pawn takes it.
    board = chess.Board("rnbqkbnr/pppp1ppp/4p3/8/8/2N5/PPPPPPPP/R1BQKBNR w KQkq - 0 2")
    category = categorize(board, M("c3d5"), M("e2e4"), M("e6d5"), 0, -300, MATE)
    assert category == HUNG_PIECE


def test_refuted_attack():
    # Bxf7+ is a check, but the king simply takes the bishop.
    board = chess.Board(
        "rnbqk1nr/pppp1ppp/8/2b1p3/2B1P3/8/PPPP1PPP/RNBQK1NR w KQkq - 2 3"
    )
    category = categorize(board, M("c4f7"), M("g1f3"), M("e8f7"), 0, -250, MATE)
    assert category == REFUTED_ATTACK


def test_allowed_tactic():
    # A quiet move that allows a check the engine likes.
    board = chess.Board("rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2")
    category = categorize(board, M("f2f3"), M("g1f3"), M("d8h4"), 0, -150, MATE)
    assert category == ALLOWED_TACTIC


def test_conversion():
    board = chess.Board("4k3/8/8/8/8/8/4P3/R3K3 w - - 0 50")
    category = categorize(board, M("a1a2"), M("e2e4"), M("e8d7"), 600, 0, MATE)
    assert category == CONVERSION


def test_missed_tactic_and_positional():
    # Black can take the e4 pawn with the knight.
    board = chess.Board(
        "rnbqkb1r/pppp1ppp/5n2/4p3/4P3/2N5/PPPP1PPP/R1BQKBNR b KQkq - 2 3"
    )
    assert (
        categorize(board, M("h7h6"), M("f6e4"), M("d2d4"), 50, -100, MATE)
        == MISSED_TACTIC
    )
    assert (
        categorize(board, M("h7h6"), M("f8c5"), M("d2d4"), 50, -100, MATE) == POSITIONAL
    )
    # Capturing the same piece with another piece is not a missed tactic.
    board = chess.Board("4k3/8/8/3p4/4P3/2N5/8/4K3 w - - 0 30")
    assert categorize(board, M("e4d5"), M("c3d5"), None, 50, -100, MATE) == POSITIONAL


@pytest.mark.parametrize(
    "control, expected",
    [
        ("600+5", (600, 5)),
        ("180", (180, 0)),
        ("1/86400", None),
        ("-", None),
        (None, None),
    ],
)
def test_parse_time_control(control, expected):
    assert parse_time_control(control) == expected


TIMED = """[TimeControl "60+1"]

1. e4 {[%clk 0:01:00]} 1... e5 {[%clk 0:00:58]} 2. Nf3 {[%clk 0:00:51]}
2... Nc6 *
"""


def test_move_times():
    times, base = move_times(read_games(TIMED)[0])
    assert base == 60
    assert times[0] == MoveTime(clock=60, spent=1, clock_before=60)
    assert times[1] == MoveTime(clock=58, spent=3, clock_before=60)
    assert times[2] == MoveTime(clock=51, spent=10, clock_before=60)
    assert times[3] is None


def test_daily_games_have_no_times():
    times, base = move_times(read_games('[TimeControl "1/86400"]\n\n1. e4 *')[0])
    assert (times, base) == ([None], None)


def test_time_flags():
    quick = MoveTime(clock=100, spent=1, clock_before=101)
    late = MoveTime(clock=10, spent=1, clock_before=11)
    slow = MoveTime(clock=100, spent=70, clock_before=170)
    assert time_flag("blunder", quick, 180, False) == IMPULSIVE
    assert time_flag("mistake", late, 180, False) == TIME_TROUBLE
    assert time_flag("best", quick, 180, False) == ""
    assert time_flag("forced", slow, 600, True) == WASTED_TIME
    assert time_flag("best", slow, 600, False) == ""
    assert time_flag("blunder", None, 600, False) == ""


def test_is_obvious():
    board = chess.Board()
    for move in ["e4", "d5", "exd5"]:
        board.push_san(move)
    assert is_obvious(board, board.parse_san("Qxd5"))
    assert not is_obvious(board, board.parse_san("Nf6"))
    # Black's king in check with a single legal move.
    assert is_obvious(chess.Board("k7/8/1K6/8/8/8/8/R7 b - - 0 1"), M("a8b8"))


def _position(pgn):
    game = read_games(pgn)[0]
    board = game.board()
    moves = list(game.mainline_moves())
    for move in moves[:-1]:
        board.push(move)
    return board, moves[-1]


def test_material_offered():
    # Bxf7+ gives a bishop for a pawn.
    assert material_offered(*_position("1. e4 e5 2. Nf3 Nc6 3. Bc4 Nf6 4. Bxf7+")) == 2
    # Taking a knight that is then taken back is a trade.
    assert material_offered(*_position("1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Bxc6")) == 0
    # A quiet move offers nothing.
    assert material_offered(*_position("1. e4 e5 2. Nf3")) == 0
    # Putting the queen where a knight takes it.
    assert material_offered(*_position("1. e4 Nf6 2. Qg4")) == 9
