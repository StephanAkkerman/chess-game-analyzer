import chess

from chess_analyzer.parse import opening_name, player_color, read_games


def test_read_games_reads_every_game(two_games_pgn):
    games = read_games(two_games_pgn)
    assert len(games) == 2
    assert len(list(games[0].mainline_moves())) == 7
    assert len(list(games[1].mainline_moves())) == 4


def test_player_color_is_case_insensitive(two_games_pgn):
    first, second = read_games(two_games_pgn)
    assert player_color(first, "alice") == chess.WHITE
    assert player_color(first, "BOB") == chess.BLACK
    assert player_color(second, "Alice") == chess.BLACK
    assert player_color(second, "dave") is None


def test_opening_name_falls_back_to_eco_url(two_games_pgn):
    first, second = read_games(two_games_pgn)
    assert opening_name(first) == "Bishops Opening"
    assert opening_name(second) is None
