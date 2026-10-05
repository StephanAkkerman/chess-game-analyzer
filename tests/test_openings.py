import struct
from unittest.mock import MagicMock

import chess
import chess.polyglot
import pytest

from chess_analyzer.openings import (
    BookMove,
    LichessExplorer,
    PolyglotBook,
    book_plies,
    find_deviation,
    rank_moves,
)
from chess_analyzer.parse import read_games


def write_polyglot(path, lines):
    """Write a Polyglot book containing every move of the given SAN lines."""
    entries = set()
    for line, weight in lines:
        board = chess.Board()
        for san in line.split():
            move = board.parse_san(san)
            raw = (
                chess.square_file(move.to_square)
                | chess.square_rank(move.to_square) << 3
                | chess.square_file(move.from_square) << 6
                | chess.square_rank(move.from_square) << 9
            )
            entries.add((chess.polyglot.zobrist_hash(board), raw, weight))
            board.push(move)
    with open(path, "wb") as f:
        for key, raw, weight in sorted(entries):
            f.write(struct.pack(">QHHI", key, raw, weight, 0))


class FakeSource:
    def __init__(self, lines):
        self.book = {}
        for line in lines:
            board = chess.Board()
            for san in line.split():
                move = board.parse_san(san)
                moves = self.book.setdefault(board.fen(), {})
                moves[move.uci()] = BookMove(uci=move.uci(), san=san, games=100)
                board.push(move)

    def moves(self, board):
        return list(self.book.get(board.fen(), {}).values())


def test_find_deviation_reports_first_move_out_of_book(scholars_mate_pgn):
    game = read_games(scholars_mate_pgn)[0]
    source = FakeSource(["e4 e5 Bc4 Nc6 Qh5 g6", "e4 e5 Bc4 Nf6"])
    deviation = find_deviation(game, source)
    assert deviation.label == "3...Nf6"
    assert deviation.color == chess.BLACK
    assert [m.san for m in deviation.alternatives] == ["g6"]


def test_find_deviation_respects_max_ply(scholars_mate_pgn):
    game = read_games(scholars_mate_pgn)[0]
    source = FakeSource(["e4 e5 Bc4 Nc6 Qh5 g6"])
    assert find_deviation(game, source, max_ply=5) is None
    assert find_deviation(game, source, max_ply=6) is not None


def test_rank_moves_prefers_score_for_the_mover():
    good_for_white = BookMove("e2e4", "e4", 100, white=60, draws=20, black=20)
    good_for_black = BookMove("d2d4", "d4", 1000, white=20, draws=20, black=60)
    assert rank_moves([good_for_black, good_for_white], chess.WHITE)[0].san == "e4"
    assert rank_moves([good_for_white, good_for_black], chess.BLACK)[0].san == "d4"
    assert good_for_white.score_for(chess.WHITE) == pytest.approx(0.7)
    # Without results (Polyglot), the most popular move wins.
    popular = BookMove("d2d4", "d4", 10)
    rare = BookMove("e2e4", "e4", 1)
    assert rank_moves([rare, popular], chess.WHITE)[0].san == "d4"


def test_polyglot_book(tmp_path, scholars_mate_pgn):
    path = tmp_path / "book.bin"
    write_polyglot(path, [("e4 e5 Bc4 Nc6 Qh5 g6", 5), ("e4 e5 Bc4 Nc6 Nf3", 9)])
    game = read_games(scholars_mate_pgn)[0]
    with PolyglotBook(str(path)) as book:
        deviation = find_deviation(game, book)
    assert deviation.label == "3...Nf6"
    assert [m.san for m in deviation.alternatives] == ["g6"]

    with PolyglotBook(str(path)) as book:
        board = chess.Board()
        for san in ["e4", "e5", "Bc4", "Nc6"]:
            board.push_san(san)
        assert [m.san for m in rank_moves(book.moves(board), chess.WHITE)] == [
            "Nf3",
            "Qh5",
        ]


def explorer_response(moves):
    response = MagicMock(status_code=200)
    response.json.return_value = {"moves": moves}
    return response


def test_lichess_explorer_filters_normalises_and_caches():
    board = chess.Board("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1")
    session = MagicMock()
    session.headers = {}
    session.get.return_value = explorer_response(
        [
            {"uci": "e1h1", "san": "O-O", "white": 50, "draws": 10, "black": 40},
            {"uci": "a1a8", "san": "Rxa8+", "white": 1, "draws": 0, "black": 0},
        ]
    )
    explorer = LichessExplorer(min_games=10, token="secret", session=session)

    moves = explorer.moves(board)
    assert [(m.uci, m.san, m.games) for m in moves] == [("e1g1", "O-O", 100)]
    assert session.headers["Authorization"] == "Bearer secret"

    url = session.get.call_args.args[0]
    params = session.get.call_args.kwargs["params"]
    assert url == "https://explorer.lichess.ovh/lichess"
    assert params["fen"] == board.fen()
    assert params["speeds"] == "blitz,rapid,classical"

    explorer.moves(board)
    assert session.get.call_count == 1


def test_lichess_explorer_rejects_unknown_database():
    with pytest.raises(ValueError):
        LichessExplorer(database="chess.com")


def test_book_plies(scholars_mate_pgn):
    game = read_games(scholars_mate_pgn)[0]
    source = FakeSource(["e4 e5 Bc4 Nc6 Qh5 g6"])
    assert book_plies(game, find_deviation(game, source)) == {1, 2, 3, 4, 5}
    # Stayed in book: every ply up to max_ply, or the whole game.
    assert book_plies(game, None, max_ply=4) == {1, 2, 3, 4}
    assert book_plies(game, None, max_ply=30) == set(range(1, 8))
