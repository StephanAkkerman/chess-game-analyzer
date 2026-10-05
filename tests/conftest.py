import pytest

SCHOLARS_MATE = """[Event "Live Chess"]
[Site "Chess.com"]
[Date "2026.10.01"]
[White "Alice"]
[Black "bob"]
[Result "1-0"]
[WhiteElo "1500"]
[BlackElo "1450"]
[TimeControl "600"]
[ECO "C23"]
[ECOUrl "https://www.chess.com/openings/Bishops-Opening"]
[Link "https://www.chess.com/game/live/1"]

1. e4 e5 2. Bc4 Nc6 3. Qh5 Nf6 4. Qxf7# 1-0
"""

FOOLS_MATE = """[White "carol"]
[Black "Alice"]
[Result "0-1"]

1. f3 e5 2. g4 Qh4# 0-1
"""


@pytest.fixture
def scholars_mate_pgn():
    return SCHOLARS_MATE


@pytest.fixture
def two_games_pgn():
    return SCHOLARS_MATE + "\n" + FOOLS_MATE
