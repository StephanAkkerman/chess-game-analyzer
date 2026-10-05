from unittest.mock import MagicMock

from chess_analyzer.fetch import BASE_URL, ChessComClient

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
