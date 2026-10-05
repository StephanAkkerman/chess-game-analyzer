import chess
from test_engine import EVALS, FakeEngine

from chess_analyzer.engine import MATE_SCORE, analyze_game
from chess_analyzer.openings import BookMove, OpeningDeviation
from chess_analyzer.parse import read_games
from chess_analyzer.report import format_eval, format_game_report


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


def test_report_without_opening_or_engine(scholars_mate_pgn):
    game = read_games(scholars_mate_pgn)[0]
    report = format_game_report(game, None, show_opening=False)
    assert "\nOpening\n" not in report
    assert "centipawn" not in report
