import requests
from conftest import SCHOLARS_MATE

from chess_analyzer.parse import read_games
from chess_analyzer.peers import (
    GAMES_PER_PLAYER,
    clock_profile,
    collect_peer_games,
    compare,
    current_rating,
    engine_profile,
    own_profiles,
    peer_insights,
    side_profile,
    summarise,
    time_controls,
    verdict,
)

CLOCK_PGN = """[White "a"]
[Black "b"]
[Result "1-0"]
[TimeControl "60"]

1. e4 {[%clk 0:00:58]} e5 {[%clk 0:00:55]} 2. Bc4 {[%clk 0:00:50]}
Nc6 {[%clk 0:00:05]} 3. Qh5 {[%clk 0:00:49]} Nf6 {[%clk 0:00:04]}
4. Qxf7# {[%clk 0:00:48]} 1-0
"""


def raw_game(white, black, url, tc="180", end_time=1790000000, **extra):
    """A Chess.com game object; ``white`` and ``black`` are (name, rating)."""
    return {
        "url": url,
        "pgn": SCHOLARS_MATE,
        "time_control": tc,
        "time_class": "blitz",
        "rules": "chess",
        "rated": True,
        "end_time": end_time,
        "white": {"username": white[0], "rating": white[1], "result": "win"},
        "black": {"username": black[0], "rating": black[1], "result": "checkmated"},
        **extra,
    }


class FakeClient:
    def __init__(self, archives):
        self.archives = archives
        self.calls = []

    def get_monthly_games(self, username, year, month):
        self.calls.append(username.lower())
        games = self.archives.get(username.lower())
        if games is None:
            raise requests.HTTPError("404")
        return games


def test_time_controls_put_daily_last():
    games = [raw_game(("me", 1), ("x", 1), str(i), tc="1/86400") for i in range(5)]
    games += [raw_game(("me", 1), ("x", 1), f"b{i}", tc="180") for i in range(2)]
    games += [raw_game(("me", 1), ("x", 1), "r", tc="600")]
    assert [c["time_control"] for c in time_controls(games)] == [
        "180",
        "600",
        "1/86400",
    ]
    assert time_controls(games)[0] == {
        "time_control": "180",
        "time_class": "blitz",
        "games": 2,
    }


def test_current_rating_comes_from_the_newest_game():
    games = [
        raw_game(("me", 1400), ("x", 1), "1", end_time=1),
        raw_game(("x", 1), ("Me", 1450), "2", end_time=3),
        raw_game(("me", 1600), ("x", 1), "3", end_time=5, tc="600"),
    ]
    assert current_rating(games, "me", "180") == 1450
    assert current_rating(games, "me", "60") is None


def test_clock_profile():
    game = read_games(CLOCK_PGN)[0]
    white = clock_profile(game, True)
    black = clock_profile(game, False)
    # White spent 2 + 8 + 1 + 1 seconds; black fell below 6 seconds.
    assert white == {"moves": 4, "seconds": 12.0, "time_trouble": False}
    assert black == {"moves": 3, "seconds": 56.0, "time_trouble": True}
    assert clock_profile(read_games(SCHOLARS_MATE)[0], True) is None


def test_side_profile():
    raw = raw_game(("a", 1500), ("b", 1450), "u", accuracies={"white": 80.5})
    raw["pgn"] = CLOCK_PGN
    raw["black"]["result"] = "timeout"
    white, black = side_profile(raw, "white"), side_profile(raw, "black")
    assert (white["rating"], white["accuracy"], white["timeout"]) == (1500, 80.5, False)
    assert (black["accuracy"], black["timeout"]) == (None, True)
    assert black["clock"]["time_trouble"]


def test_collect_peer_games_walks_from_the_players_opponents():
    mine = [
        raw_game(("me", 1500), ("alice", 1510), "m1", end_time=2),
        raw_game(("bob", 1490), ("me", 1500), "m2", end_time=1),
        # Another time control: its opponent is not a starting point.
        raw_game(("me", 1500), ("zoe", 1500), "m3", tc="600"),
    ]
    alice = [
        raw_game(("alice", 1510), ("carol", 1700), "a1", end_time=9),
        raw_game(("alice", 1510), ("dave", 1450), "a2", end_time=8),
        raw_game(("me", 1500), ("alice", 1510), "m1"),  # the player's own game
        raw_game(("alice", 1510), ("erin", 1500), "a3", tc="600"),
        raw_game(("alice", 1300), ("fred", 1350), "a4"),  # nobody in range
    ]
    archives = {
        "alice": alice,
        "dave": [raw_game(("dave", 1450), ("gina", 1520), "d1")],
        "carol": [raw_game(("carol", 1700), ("hank", 1500), "c1")],
    }
    client = FakeClient(archives)
    games = collect_peer_games(client, "Me", mine, "180", target=1500)
    # The player closest to 1500 comes next: alice, then bob (whose archive
    # is missing), dave, his opponent gina, fred and only then carol.
    assert client.calls == ["alice", "bob", "dave", "gina", "fred", "carol", "hank"]
    assert [g["url"] for g in games] == ["a1", "a2", "d1", "c1"]
    assert list(games[0]["sides"]) == ["white"]  # carol is out of range
    assert set(games[1]["sides"]) == {"white", "black"}
    assert games[0]["sides"]["white"]["username"] == "alice"


def test_collect_peer_games_limits():
    mine = [raw_game(("me", 1500), ("alice", 1500), "m1")]
    many = [
        raw_game(("alice", 1500), (f"p{i}", 1500), f"a{i}", end_time=100 - i)
        for i in range(GAMES_PER_PLAYER + 5)
    ]
    client = FakeClient({"alice": many})
    games = collect_peer_games(client, "me", mine, "180", target=1500)
    assert len(games) == GAMES_PER_PLAYER
    assert games[0]["url"] == "a0"  # newest first
    games = collect_peer_games(client, "me", mine, "180", target=1500, limit=3)
    assert len(games) == 3
    client = FakeClient({"alice": many})
    collect_peer_games(client, "me", mine, "180", target=1500, max_archives=2)
    assert len(client.calls) == 2


def test_own_profiles():
    mine = [
        raw_game(("me", 1500), ("x", 1500), "1"),
        raw_game(("x", 1500), ("me", 1500), "2", tc="600"),
    ]
    assert [p["color"] for p in own_profiles(mine, "ME", "180")] == ["white"]


def analysis(counts, categories=None, phases=None, critical=()):
    moves = [{"classification": c} for c in critical]
    return {
        "moves": moves,
        "summary": {
            "white": {
                "counts": counts,
                "categories": categories or {},
                "phases": phases or {},
                "critical": list(range(1, len(moves) + 1)),
            }
        },
    }


def test_engine_profile():
    result = analysis(
        {"book": 5, "forced": 1, "best": 20, "blunder": 2, "mistake": 3},
        {"missed_tactic": 1, "missed_mate": 1, "hung_piece": 2, "positional": 1},
        {"endgame": {"acpl": 50, "moves": 10}},
        critical=["great", "mistake", "best"],
    )
    profile = engine_profile(result, "white")
    assert profile == {
        "decisions": 25,
        "blunders": 2,
        "missed_tactics": 2,
        "allowed_tactics": 2,
        "phases": {"endgame": {"acpl": 50, "moves": 10}},
        "critical": 3,
        "critical_found": 2,
    }


def profile(seconds=60.0, moves=30, trouble=False, timeout=False, accuracy=None):
    return {
        "accuracy": accuracy,
        "timeout": timeout,
        "clock": {"moves": moves, "seconds": seconds, "time_trouble": trouble},
    }


def engine(endgame=40, opening=20, decisions=40, blunders=1, critical=(2, 1)):
    return {
        "decisions": decisions,
        "blunders": blunders,
        "missed_tactics": 0,
        "allowed_tactics": 1,
        "phases": {
            "opening": {"acpl": opening, "moves": 10},
            "endgame": {"acpl": endgame, "moves": 15},
        },
        "critical": critical[0],
        "critical_found": critical[1],
    }


def test_summarise_pools_the_numbers():
    summary = summarise(
        [profile(60, 30, True), profile(30, 30, timeout=True, accuracy=80)],
        [engine(endgame=40), engine(endgame=60, blunders=3)],
    )
    assert summary["seconds"] == {"value": 1.5, "n": 2}
    assert summary["time_trouble"] == {"value": 0.5, "n": 2}
    assert summary["timeouts"] == {"value": 0.5, "n": 2}
    assert summary["accuracy"] == {"value": 80, "n": 1}
    assert summary["endgame"] == {"value": 50, "n": 30}
    assert summary["middlegame"] is None
    assert summary["blunders"] == {"value": 5.0, "n": 80}
    assert summary["critical"] == {"value": 0.5, "n": 4}
    assert summarise([], [])["seconds"] is None


def test_verdict():
    def m(value, n=1000):
        return {"value": value, "n": n}

    assert verdict("cpl", "lower", m(30), m(50)) == "ahead"
    assert verdict("cpl", "lower", m(70), m(50)) == "behind"
    assert verdict("cpl", "lower", m(55), m(50)) == "level"  # under 20%
    assert verdict("cpl", "lower", m(6), m(3)) == "level"  # under 5 centipawns
    assert verdict("cpl", "lower", m(70, 10), m(50)) is None  # too few moves
    assert verdict("share", "lower", m(0.3), m(0.15)) == "behind"
    assert verdict("share", "higher", m(0.3), m(0.15)) == "ahead"
    assert verdict("points", "higher", m(70), m(72)) == "level"
    assert verdict("seconds", None, m(3), m(5)) is None
    assert verdict("cpl", "lower", None, m(5)) is None


def test_insights_name_the_weakest_phase():
    you = summarise([profile()] * 10, [engine(endgame=80, opening=10)] * 10)
    peers = summarise([profile()] * 20, [engine(endgame=40, opening=30)] * 20)
    rows = compare(you, peers)
    by_key = {r["key"]: r for r in rows}
    assert by_key["endgame"]["verdict"] == "behind"
    assert by_key["opening"]["verdict"] == "ahead"
    assert by_key["seconds"]["verdict"] is None
    assert "middlegame" not in by_key
    lines = peer_insights(rows, 1500, 1500)
    assert lines[0].startswith("Your endgame is your weakest phase compared with")
    assert "players at your rating" in lines[0]
    assert "they lose 40" in lines[0]
    assert "Your opening is already better" in lines[1]
    assert "studying openings gains you less" in lines[1]


def test_insights_for_stronger_players_and_the_clock():
    you = summarise([profile(trouble=True)] * 10, [])
    peers = summarise([profile()] * 20, [])
    lines = peer_insights(compare(you, peers), 1700, 1500)
    assert lines == [
        (
            "You get into time trouble in 100% of your games, players rated "
            "around 1700 in 0%: play faster in the opening and middlegame."
        )
    ]
