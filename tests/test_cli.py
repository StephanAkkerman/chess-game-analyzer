import pytest
from test_openings import write_polyglot

from chess_analyzer import cli
from chess_analyzer.engine import find_engine


@pytest.fixture
def pgn_file(tmp_path, two_games_pgn):
    path = tmp_path / "games.pgn"
    path.write_text(two_games_pgn)
    return str(path)


def test_cli_with_book_and_no_engine(tmp_path, pgn_file, capsys):
    book = tmp_path / "book.bin"
    write_polyglot(book, [("e4 e5 Bc4 Nc6 Qh5 g6", 1), ("e4 e5 Nf3", 1)])
    code = cli.main(["alice", "--pgn", pgn_file, "--no-engine", "--book", str(book)])
    out = capsys.readouterr().out
    assert code == 0
    assert "Your opponent left theory with 3...Nf6." in out
    # Fool's Mate starts with 1.f3, which is not in the book.
    assert "Your opponent left theory with 1.f3." in out
    assert "centipawn" not in out


def test_cli_max_games(pgn_file, capsys):
    code = cli.main(
        ["alice", "--pgn", pgn_file, "--no-engine", "--explorer", "none"]
        + ["--max-games", "1"]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "Alice (1500) vs bob" in out
    assert "carol" not in out


def test_cli_reports_missing_engine(pgn_file, monkeypatch, capsys):
    monkeypatch.setattr(cli, "find_engine", lambda path: None)
    assert cli.main(["alice", "--pgn", pgn_file, "--explorer", "none"]) == 1
    assert "Stockfish not found" in capsys.readouterr().err


@pytest.mark.skipif(find_engine() is None, reason="Stockfish is not installed")
def test_cli_with_stockfish(pgn_file, capsys):
    code = cli.main(["alice", "--pgn", pgn_file, "--explorer", "none", "--depth", "10"])
    out = capsys.readouterr().out
    assert code == 0
    assert "White: average centipawn loss" in out
    assert "Black: average centipawn loss" in out
