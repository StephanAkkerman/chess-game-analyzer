from datetime import date

import chess
import chess.engine
import pytest

from chess_analyzer.engine import analyze_game
from chess_analyzer.parse import read_games
from chess_analyzer.puzzles import (
    INTERVALS,
    build_puzzle,
    candidates,
    daily_set,
    position_key,
    review,
    solution_line,
)
from chess_analyzer.serialize import result_to_dict

# White misses Legal's mate with 5.h3: 5.Nxe5 Bxd1 6.Bxf7+ Ke7 7.Nd5#.
LEGAL_PGN = (
    '[White "alice"]\n[Black "bob"]\n[Date "2026.10.01"]\n\n'
    "1. e4 e5 2. Nf3 d6 3. Bc4 Bg4 4. Nc3 g6 5. h3 *"
)
LEGAL_FEN = "rn1qkbnr/ppp2p1p/3p2p1/4p3/2B1P1b1/2N2N2/PPPP1PPP/R1BQK2R w KQkq - 0 5"
LEGAL_LINE = ["f3e5", "g4d1", "c4f7", "e8e7", "c3d5"]


class LineEngine:
    """Answer each search with the game move, except before 5.h3."""

    def __init__(self, game):
        self.moves = list(game.mainline_moves())

    def analyse(self, board, limit, root_moves=None):
        ply = len(board.move_stack)
        if ply == 8:
            score, line = 500, LEGAL_LINE
        elif ply < len(self.moves):
            score, line = 0, [self.moves[ply].uci()]
        else:
            score, line = 0, ["g4f3"]
        if root_moves is not None:
            line = [root_moves[0].uci()]
        return {
            "score": chess.engine.PovScore(chess.engine.Cp(score), chess.WHITE),
            "pv": [chess.Move.from_uci(m) for m in line],
        }


@pytest.fixture
def legal_result():
    game = read_games(LEGAL_PGN)[0]
    analysis = analyze_game(game, LineEngine(game))
    return result_to_dict(game, analysis, None, False, None, "depth 16")


def test_position_key_ignores_move_counters():
    assert position_key(LEGAL_FEN) == position_key(LEGAL_FEN.replace("0 5", "3 9"))
    assert position_key(LEGAL_FEN) != position_key(chess.STARTING_FEN)


@pytest.mark.parametrize(
    "line, expected",
    [
        # Ends in mate: all of it.
        (LEGAL_LINE, LEGAL_LINE),
        # Ends with the solver's move: the opponent's last move goes.
        (LEGAL_LINE[:4], LEGAL_LINE[:3]),
        # The quiet move after the point of the tactic goes.
        (["f3e5", "g4d1", "d2d3"], ["f3e5"]),
        # At most three moves of the solver.
        (LEGAL_LINE + ["e7e6", "d5c7"], LEGAL_LINE),
        # Illegal moves end the line.
        (["f3e5", "e8e7"], ["f3e5"]),
        (["e2e5"], []),
    ],
)
def test_solution_line(line, expected):
    board = chess.Board(LEGAL_FEN)
    assert [m.uci() for m in solution_line(board, line)] == expected


def test_missed_move_keeps_the_engine_line(legal_result):
    h3 = legal_result["moves"][8]
    assert h3["classification"] in ("mistake", "miss", "blunder")
    assert h3["best_line"] == LEGAL_LINE
    # Moves without an error keep no line.
    assert legal_result["moves"][0]["best_line"] == []

    [puzzle] = candidates(legal_result, "white")
    assert (puzzle["ply"], puzzle["fen"], puzzle["line"]) == (9, LEGAL_FEN, LEGAL_LINE)
    assert candidates(legal_result, "black") == []


def test_build_puzzle(legal_result):
    puzzle = build_puzzle(legal_result, 9, "abc")
    assert (puzzle["id"], puzzle["color"], puzzle["fen"]) == ("abc", "white", LEGAL_FEN)
    assert puzzle["played"]["san"] == "h3"
    assert [m["san"] for m in puzzle["solution"]] == [
        "Nxe5",
        "Bxd1",
        "Bxf7+",
        "Ke7",
        "Nd5#",
    ]
    assert chess.Board(puzzle["solution"][-1]["fen"]).is_checkmate()
    # One step per move of the solver, with its legal moves and mates.
    assert len(puzzle["steps"]) == 3
    assert puzzle["steps"][0]["legal"]["f3e5"] == "Nxe5"
    assert puzzle["steps"][0]["mates"] == []
    assert puzzle["steps"][2]["mates"] == ["c3d5"]
    # The four game moves before the puzzle.
    lead_in = puzzle["lead_in"]
    assert [m["san"] for m in lead_in["moves"]] == ["Bc4", "Bg4", "Nc3", "g6"]
    assert lead_in["fen"] == legal_result["moves"][3]["fen"]
    assert lead_in["moves"][-1]["fen"] == LEGAL_FEN

    assert build_puzzle(legal_result, 99) is None
    assert build_puzzle(legal_result, 0) is None


def test_old_results_without_lines_make_one_move_puzzles(legal_result):
    for m in legal_result["moves"]:
        del m["best_line"]
    puzzle = build_puzzle(legal_result, 9)
    assert [m["uci"] for m in puzzle["solution"]] == ["f3e5"]
    assert candidates(legal_result, "white")[0]["line"] == ["f3e5"]


def test_review_schedule():
    day = date(2026, 10, 7)
    # Solved: back in 3 days, then a week.
    first = review(None, True, day)
    assert (first["step"], first["due"], first["first"]) == (
        1,
        "2026-10-10",
        "2026-10-07",
    )
    second = review(first, True, date(2026, 10, 10))
    assert (second["step"], second["due"]) == (2, "2026-10-17")
    # Solving it again before it is due changes nothing but the counts.
    early = review(second, True, date(2026, 10, 11))
    assert (early["step"], early["due"], early["solved"]) == (2, "2026-10-17", 3)
    # Failed: back tomorrow, from the start.
    failed = review(second, False, date(2026, 10, 17))
    assert (failed["step"], failed["due"], failed["failed"]) == (0, "2026-10-18", 1)
    assert review(failed, True, date(2026, 10, 18))["due"] == "2026-10-21"
    assert review(None, False, day)["due"] == "2026-10-08"


def test_review_intervals_stop_growing():
    day = date(2026, 1, 1)
    data = None
    for _ in range(len(INTERVALS) + 3):
        data = review(data, True, day)
        day = date.fromisoformat(data["due"])
    assert data["step"] == len(INTERVALS) - 1


def test_daily_set():
    today = date(2026, 10, 7)
    puzzles = [{"key": k} for k in "abcdefa"]
    reviews = {
        "a": {"step": 1, "due": "2026-10-06", "last": "2026-10-03"},
        "b": {"step": 3, "due": "2026-10-20", "last": "2026-10-07"},
        "c": {"step": 0, "due": "2026-10-05", "first": "2026-10-07"},
        "d": {"step": 2, "due": "2026-10-08"},
    }
    chosen = daily_set(puzzles, reviews, today, new_per_day=2)
    # The longest overdue first; the repeated position counts once.
    assert [p["key"] for p in chosen["due"]] == ["c", "a"]
    # One new puzzle was started today already.
    assert [p["key"] for p in chosen["new"]] == ["e"]
    assert (chosen["total"], chosen["unseen"], chosen["learned"]) == (6, 2, 1)
    assert (chosen["done_today"], chosen["tomorrow"]) == (1, 1)
