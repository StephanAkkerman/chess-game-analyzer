import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

from chess_analyzer import fetch
from chess_analyzer.fetch import (
    BASE_URL,
    LICHESS_URL,
    ChessComClient,
    LichessClient,
    _month_start,
    lichess_game,
)
from chess_analyzer.parse import read_games

ARCHIVES = [
    f"{BASE_URL}/player/alice/games/2026/08",
    f"{BASE_URL}/player/alice/games/2026/09",
    f"{BASE_URL}/player/alice/games/2026/10",
]


def game(end_time, rules="chess", time_class="blitz"):
    return {
        "pgn": f'[Event "{end_time}"]\n\n1. e4 *',
        "end_time": end_time,
        "rules": rules,
        "time_class": time_class,
    }


RESPONSES = {
    f"{BASE_URL}/player/alice/games/archives": {"archives": ARCHIVES},
    ARCHIVES[0]: {"games": [game(1)]},
    ARCHIVES[1]: {"games": [game(2), game(3, time_class="rapid")]},
    ARCHIVES[2]: {"games": [game(5), game(4, rules="chess960"), game(6)]},
}


def make_client():
    session = MagicMock()
    session.headers = {}

    def get(url, timeout):
        response = MagicMock()
        response.json.return_value = RESPONSES[url]
        return response

    session.get.side_effect = get
    return ChessComClient(session=session), session


def test_sends_user_agent():
    _, session = make_client()
    assert "chess-game-analyzer" in session.headers["User-Agent"]


def test_lowercases_username_in_url():
    client, session = make_client()
    assert client.get_archives("Alice") == ARCHIVES
    session.get.assert_called_with(
        f"{BASE_URL}/player/alice/games/archives", timeout=client.timeout
    )


def test_recent_games_are_newest_first_and_skip_variants():
    client, _ = make_client()
    games = client.get_recent_games("alice", months=2)
    assert [g["end_time"] for g in games] == [6, 5, 3, 2]


def test_recent_games_respects_max_games_and_time_class():
    client, session = make_client()
    games = client.get_recent_games("alice", months=3, max_games=3)
    assert [g["end_time"] for g in games] == [6, 5, 3]
    # The oldest archive is never requested once enough games were found.
    assert ARCHIVES[0] not in [c.args[0] for c in session.get.call_args_list]

    games = client.get_recent_games("alice", months=3, time_class="rapid")
    assert [g["end_time"] for g in games] == [3]


def lichess_raw(game_id, last_move_at, **extra):
    raw = {
        "id": game_id,
        "rated": True,
        "variant": "standard",
        "speed": "blitz",
        "createdAt": last_move_at - 300_000,
        "lastMoveAt": last_move_at,
        "status": "resign",
        "winner": "white",
        "clock": {"initial": 180, "increment": 2, "totalTime": 260},
        "players": {
            "white": {"user": {"name": "Alice", "id": "alice"}, "rating": 1500},
            "black": {
                "user": {"name": "bob", "id": "bob"},
                "rating": 1450,
                "analysis": {"accuracy": 81},
            },
        },
        "pgn": f'[Event "Rated blitz game"]\n[Site "{LICHESS_URL}/{game_id}"]\n'
        '[White "Alice"]\n[Black "bob"]\n\n'
        "1. e4 e5 2. Qh5 Nc6 3. Bc4 Nf6 4. Qxf7# 1-0\n",
    }
    raw.update(extra)
    return raw


def make_lichess_client(lines, token=None):
    session = MagicMock()
    session.headers = {}
    response = MagicMock()
    response.text = "\n".join(json.dumps(line) for line in lines) + "\n"
    session.get.return_value = response
    return LichessClient(token=token, session=session), session


def test_lichess_game_matches_chesscom_shape():
    game = lichess_game(lichess_raw("abcd1234", 1_700_000_000_000))
    assert game["url"] == f"{LICHESS_URL}/abcd1234"
    assert game["end_time"] == 1_700_000_000
    assert (game["time_class"], game["time_control"]) == ("blitz", "180+2")
    assert game["white"] == {"username": "Alice", "rating": 1500, "result": "win"}
    assert game["black"]["result"] == "resigned"
    assert game["accuracies"] == {"black": 81}
    # The PGN gets a Link tag, which identifies the game like on Chess.com.
    (parsed,) = read_games(game["pgn"])
    assert parsed.headers["Link"] == game["url"]
    assert parsed.headers["White"] == "Alice"


def test_lichess_results_and_unfinished_games():
    draw = lichess_game(lichess_raw("a", 1, status="stalemate", winner=None))
    assert draw["white"]["result"] == draw["black"]["result"] == "stalemate"
    flagged = lichess_game(lichess_raw("b", 1, status="outoftime", winner="black"))
    assert flagged["white"]["result"] == "timeout"
    ai = lichess_raw("c", 1)
    ai["players"]["black"] = {"aiLevel": 3}
    assert lichess_game(ai)["black"]["username"] == "lichess AI level 3"
    assert lichess_game(lichess_raw("d", 1, status="aborted")) is None
    corres = lichess_game(lichess_raw("e", 1, speed="correspondence", clock=None))
    assert (corres["time_class"], corres["time_control"]) == ("daily", "-")


def test_lichess_recent_games_are_newest_first_and_skip_variants():
    client, session = make_lichess_client(
        [
            lichess_raw("old", 1_000),
            lichess_raw("new", 3_000),
            lichess_raw("pos", 2_000, variant="fromPosition"),
        ],
        token="secret",
    )
    games = client.get_recent_games("Alice", months=2, max_games=5, time_class="rapid")
    assert [g["url"].rsplit("/", 1)[1] for g in games] == ["new", "old"]
    assert session.headers["Authorization"] == "Bearer secret"
    call = session.get.call_args
    assert call.args[0] == f"{LICHESS_URL}/api/games/user/Alice"
    assert call.kwargs["params"]["max"] == 5
    assert call.kwargs["params"]["perfType"] == "rapid,classical"
    assert call.kwargs["headers"]["Accept"] == "application/x-ndjson"


def test_month_start_counts_calendar_months():
    now = datetime(2026, 2, 15, tzinfo=timezone.utc)
    assert _month_start(1, now) == datetime(2026, 2, 1, tzinfo=timezone.utc)
    assert _month_start(3, now) == datetime(2025, 12, 1, tzinfo=timezone.utc)


def test_make_client():
    assert isinstance(fetch.make_client("chess.com"), ChessComClient)
    assert isinstance(fetch.make_client("lichess"), LichessClient)
