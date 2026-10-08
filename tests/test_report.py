import chess
from test_engine import CRITICAL_EVALS, CRITICAL_PGN, EVALS, FakeEngine

from chess_analyzer.engine import MATE_SCORE, analyze_game
from chess_analyzer.openings import BookMove, OpeningDeviation
from chess_analyzer.parse import read_games
from chess_analyzer.report import (
    format_eval,
    format_game_report,
    format_player_stats,
    format_puzzles_pgn,
)
from chess_analyzer.serialize import result_to_dict
from chess_analyzer.stats import game_record, player_stats


def test_format_eval():
    assert format_eval(125) == "+1.25"
    assert format_eval(-40) == "-0.40"
    assert format_eval(MATE_SCORE - 3) == "+M3"
    assert format_eval(-MATE_SCORE + 2) == "-M2"
    assert format_eval(MATE_SCORE) == "+#"


def test_report_for_player(scholars_mate_pgn):
    game = read_games(scholars_mate_pgn)[0]
    analysis = analyze_game(game, FakeEngine(EVALS))
    deviation = OpeningDeviation(
        ply=6,
        color=chess.BLACK,
        played_san="Nf6",
        fen="",
        alternatives=[BookMove("g7g6", "g6", 200, white=80, draws=20, black=100)],
    )
    report = format_game_report(game, chess.BLACK, analysis, deviation)

    assert "Alice (1500) vs bob (1450)  1-0" in report
    assert "Bishops Opening" in report
    assert "You left theory with 3...Nf6." in report
    assert "g6       55% score over 200 games" in report
    assert "Black: average centipawn loss 340" in report
    assert "3...Nf6      blunder  -0.20 -> +M1  best was g6" in report
    # Only the requested side is reported.
    assert "White:" not in report
    assert "Engine searched 7 of 8 positions (1 terminal)." in report


def test_format_eval_for_book_and_tablebase():
    assert format_eval(None) == "book"
    assert format_eval(2000, "tablebase") == "TB 1-0"
    assert format_eval(0, "tablebase") == "TB draw"
    assert format_eval(-2000, "tablebase") == "TB 0-1"


def test_report_without_opening_or_engine(scholars_mate_pgn):
    game = read_games(scholars_mate_pgn)[0]
    report = format_game_report(game, None, show_opening=False)
    assert "\nOpening\n" not in report
    assert "centipawn" not in report


def test_critical_moments_in_reports_and_puzzles():
    game = read_games(CRITICAL_PGN)[0]
    game.headers.update(White="alice", Black="bob", Date="2026.10.01")
    analysis = analyze_game(game, FakeEngine(CRITICAL_EVALS))

    report = format_game_report(game, chess.BLACK, analysis, show_opening=False)
    assert "Critical moments (only one move kept the balance):" in report
    assert "2...Nc6      found; d6 would give +3.30" in report
    assert "3...Bc5      missed, best was Nf6; Bc5 would give +4.00" in report

    result = result_to_dict(game, analysis, None, False, None, "depth 16")
    assert result["summary"]["black"]["critical"] == [4, 6]
    record = game_record(result, "bob", "abc")
    stats = player_stats([record])
    assert stats["critical"] == {"total": 2, "found": 1}
    # The found critical moment, 2...Nc6, makes no puzzle.
    [puzzle] = stats["puzzles"]
    assert (puzzle["id"], puzzle["label"], puzzle["kind"]) == (
        "abc",
        "3...Bc5",
        "critical",
    )
    assert puzzle["fen"] == analysis.moves[5].fen_before
    text = format_player_stats(stats, "bob")
    assert "you found the only good move in 1 of 2" in text
    assert "3...Bc5      critical" in text

    pgn = format_puzzles_pgn(stats["puzzles"])
    [game] = read_games(pgn)
    assert game.board().fen() == puzzle["fen"]
    assert game.headers["Event"] == "Puzzle, move 3, Black to move"
    assert game.next().move.uci() == "g8f6"
    assert "3...Bc5 was played" in game.next().comment
