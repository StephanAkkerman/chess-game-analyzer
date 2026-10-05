import time
from unittest.mock import MagicMock

import pytest
import requests
from fastapi.testclient import TestClient
from test_engine import EVALS, FakeEngine
from test_openings import FakeSource

from chess_analyzer.web.app import create_app, game_summary
from chess_analyzer.web.settings import Settings


class QuittableEngine(FakeEngine):
    def quit(self):
        pass


class BrokenSource:
    def moves(self, board):
        raise requests.ConnectionError("offline")


def wait_for(client, job_id, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/analyses/{job_id}").json()
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.02)
    raise AssertionError("analysis did not finish")


def make_client(tmp_path, opening=None, chesscom=None, **settings):
    app = create_app(
        Settings(data_dir=tmp_path, **settings),
        engine_factory=lambda: QuittableEngine(EVALS),
        opening_factory=lambda: opening,
        chesscom=chesscom or MagicMock(),
    )
    return TestClient(app)


def test_analysis_round_trip(tmp_path, scholars_mate_pgn):
    source = FakeSource(["e4 e5 Bc4 Nc6 Qh5 g6"])
    with make_client(tmp_path, opening=source) as client:
        response = client.post("/api/analyses", json={"pgn": scholars_mate_pgn})
        assert response.status_code == 202
        job = wait_for(client, response.json()["id"])

    assert job["status"] == "done"
    assert (job["done"], job["total"]) == (7, 7)
    result = job["result"]
    assert result["headers"]["White"] == "Alice"
    assert result["opening"]["name"] == "Bishops Opening"
    assert result["opening"]["deviation"]["label"] == "3...Nf6"
    assert result["opening"]["deviation"]["alternatives"][0]["san"] == "g6"
    nf6 = result["moves"][5]
    assert nf6["classification"] == "blunder"
    assert (nf6["uci"], nf6["best_uci"], nf6["best_san"]) == ("g8f6", "g7g6", "g6")
    assert result["moves"][-1]["fen"].startswith("r1bqkb1r/pppp1Qpp/")
    assert result["summary"]["black"]["worst"] == [6]
    assert result["summary"]["white"]["counts"]["inaccuracy"] == 1


def test_same_game_is_analysed_once(tmp_path, scholars_mate_pgn):
    with make_client(tmp_path) as client:
        first = client.post("/api/analyses", json={"pgn": scholars_mate_pgn}).json()
        wait_for(client, first["id"])
        second = client.post("/api/analyses", json={"pgn": scholars_mate_pgn}).json()
    assert second["id"] == first["id"]
    assert second["status"] == "done"


def test_results_survive_a_restart(tmp_path, scholars_mate_pgn):
    with make_client(tmp_path) as client:
        job_id = client.post("/api/analyses", json={"pgn": scholars_mate_pgn}).json()[
            "id"
        ]
        wait_for(client, job_id)
    with make_client(tmp_path) as client:
        assert client.get(f"/api/analyses/{job_id}").json()["status"] == "done"


def test_opening_failure_does_not_fail_the_analysis(tmp_path, scholars_mate_pgn):
    with make_client(tmp_path, opening=BrokenSource()) as client:
        job_id = client.post("/api/analyses", json={"pgn": scholars_mate_pgn}).json()[
            "id"
        ]
        job = wait_for(client, job_id)
    assert job["status"] == "done"
    assert job["result"]["opening"]["checked"] is False
    assert "could not be reached" in job["result"]["opening"]["error"]


@pytest.mark.parametrize(
    "pgn, message",
    [
        ("", "No game found"),
        ('[White "a"]\n\n*', "no moves"),
        ("1. e4 e5 2. Ke3", "Could not read"),
        ('[Variant "Chess960"]\n\n1. e4 *', "not supported"),
    ],
)
def test_invalid_pgn_is_rejected(tmp_path, pgn, message):
    with make_client(tmp_path) as client:
        response = client.post("/api/analyses", json={"pgn": pgn})
    assert response.status_code == 422
    assert message in response.json()["detail"]


def test_long_games_are_rejected(tmp_path, scholars_mate_pgn):
    with make_client(tmp_path, max_plies=5) as client:
        response = client.post("/api/analyses", json={"pgn": scholars_mate_pgn})
    assert response.status_code == 422


def test_unknown_analysis_is_404(tmp_path):
    with make_client(tmp_path) as client:
        assert client.get("/api/analyses/nope").status_code == 404


def test_access_code(tmp_path, scholars_mate_pgn):
    with make_client(tmp_path, access_code="knight") as client:
        assert client.get("/api/config").json()["access_code_required"] is True
        assert client.get("/api/access").status_code == 401
        response = client.post("/api/analyses", json={"pgn": scholars_mate_pgn})
        assert response.status_code == 401
        response = client.post(
            "/api/analyses",
            json={"pgn": scholars_mate_pgn},
            headers={"X-Access-Code": "knight"},
        )
        assert response.status_code == 202
        # Anyone with the link can view a finished analysis.
        assert client.get(f"/api/analyses/{response.json()['id']}").status_code == 200


def chesscom_game(white, black, white_result, black_result):
    return {
        "url": "https://www.chess.com/game/live/1",
        "pgn": "1. e4 *",
        "end_time": 1_700_000_000,
        "time_class": "blitz",
        "time_control": "180+2",
        "rated": True,
        "white": {"username": white, "rating": 1500, "result": white_result},
        "black": {"username": black, "rating": 1400, "result": black_result},
    }


def test_game_summary_from_players_point_of_view():
    win = game_summary(chesscom_game("Alice", "bob", "win", "checkmated"), "alice")
    assert (win["color"], win["result"], win["rating"]) == ("white", "win", 1500)
    assert win["opponent"] == {"username": "bob", "rating": 1500 - 100}
    loss = game_summary(chesscom_game("Alice", "bob", "win", "resigned"), "bob")
    assert (loss["color"], loss["result"]) == ("black", "loss")
    draw = game_summary(chesscom_game("Alice", "bob", "agreed", "agreed"), "bob")
    assert draw["result"] == "draw"


def test_player_games_are_cached(tmp_path):
    chesscom = MagicMock()
    chesscom.get_recent_games.return_value = [
        chesscom_game("Alice", "bob", "win", "resigned")
    ]
    with make_client(tmp_path, chesscom=chesscom) as client:
        data = client.get("/api/players/Alice/games").json()
        client.get("/api/players/alice/games")
    assert data["games"][0]["opponent"]["username"] == "bob"
    chesscom.get_recent_games.assert_called_once_with("Alice", months=1)


def test_unknown_player_is_404(tmp_path):
    chesscom = MagicMock()
    response = MagicMock(status_code=404)
    chesscom.get_recent_games.side_effect = requests.HTTPError(response=response)
    with make_client(tmp_path, chesscom=chesscom) as client:
        assert client.get("/api/players/nobody/games").status_code == 404


def test_frontend_and_pieces_are_served(tmp_path):
    with make_client(tmp_path) as client:
        assert "Chess Analyzer" in client.get("/").text
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/manifest.webmanifest").status_code == 200
        piece = client.get("/api/pieces/bN.svg")
        assert piece.headers["content-type"] == "image/svg+xml"
        assert "black knight" in piece.text
        assert client.get("/api/pieces/xx.svg").status_code == 404
