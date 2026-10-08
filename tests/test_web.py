import time
from unittest.mock import MagicMock

import pytest
import requests
from fastapi.testclient import TestClient
from test_engine import CRITICAL_EVALS, CRITICAL_PGN, EVALS, FakeEngine
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


def make_client(
    tmp_path, opening=None, chesscom=None, evals=EVALS, lichess=None, **settings
):
    app = create_app(
        Settings(data_dir=tmp_path, **settings),
        engine_factory=lambda: QuittableEngine(evals),
        opening_factory=lambda: opening,
        chesscom=chesscom or MagicMock(),
        lichess=lichess or MagicMock(),
    )
    return TestClient(app)


def test_analysis_round_trip(tmp_path, scholars_mate_pgn):
    source = FakeSource(["e4 e5 Bc4 Nc6 Qh5 g6"])
    with make_client(tmp_path, opening=source) as client:
        response = client.post("/api/analyses", json={"pgn": scholars_mate_pgn})
        assert response.status_code == 202
        job = wait_for(client, response.json()["id"])

    assert job["status"] == "done"
    # Five book moves need no evaluation: only the three positions from the
    # deviation on are looked at (the last one is checkmate). The one before
    # 3...Nf6 is searched again, to see whether it is a critical moment.
    assert (job["done"], job["total"]) == (4, 4)
    result = job["result"]
    assert result["positions"] == {"book": 5, "engine": 2, "terminal": 1}
    assert [m["classification"] for m in result["moves"][:5]] == ["book"] * 5
    assert result["moves"][0]["eval_after"] is None
    assert result["headers"]["White"] == "Alice"
    assert result["opening"]["name"] == "Bishops Opening"
    assert result["opening"]["deviation"]["label"] == "3...Nf6"
    assert result["opening"]["deviation"]["alternatives"][0]["san"] == "g6"
    nf6 = result["moves"][5]
    assert nf6["classification"] == "blunder"
    assert (nf6["uci"], nf6["best_uci"], nf6["best_san"]) == ("g8f6", "g7g6", "g6")
    assert result["moves"][-1]["fen"].startswith("r1bqkb1r/pppp1Qpp/")
    assert result["summary"]["black"]["worst"] == [6]
    assert result["summary"]["white"]["counts"]["book"] == 3


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


def test_player_games_from_lichess(tmp_path):
    chesscom, lichess = MagicMock(), MagicMock()
    lichess.get_recent_games.return_value = [
        chesscom_game("Alice", "bob", "win", "resigned")
    ]
    with make_client(tmp_path, chesscom=chesscom, lichess=lichess) as client:
        data = client.get("/api/players/Alice/games?site=lichess&months=3").json()
        assert client.get("/api/players/Alice/games?site=fics").status_code == 422
    assert data["site"] == "lichess"
    assert data["games"][0]["result"] == "win"
    lichess.get_recent_games.assert_called_once_with("Alice", months=3)
    chesscom.get_recent_games.assert_not_called()


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


def test_player_stats(tmp_path, scholars_mate_pgn):
    with make_client(tmp_path) as client:
        assert client.get("/api/players/alice/stats").json()["games"] == []
        job_id = client.post("/api/analyses", json={"pgn": scholars_mate_pgn}).json()[
            "id"
        ]
        wait_for(client, job_id)
        alice = client.get("/api/players/Alice/stats").json()
        bob = client.get("/api/players/bob/stats").json()

    assert [g["id"] for g in alice["games"]] == [job_id]
    assert alice["games"][0]["result"] == "win"
    assert bob["games"][0]["result"] == "loss"
    assert bob["categories"] == {"allowed_mate": 1}


def test_player_stats_count_each_game_once(tmp_path, scholars_mate_pgn):
    with make_client(tmp_path) as client:
        store = client.app.state.store
        result = {
            "headers": {"White": "Alice", "Black": "bob", "Link": "https://x/1"},
            "summary": {"white": {"acpl": 10}, "black": {"acpl": 20}},
        }
        for job_id, acpl in (("old", 50), ("new", 10)):
            store.create(job_id, job_id, scholars_mate_pgn, 7)
            result["summary"]["white"]["acpl"] = acpl
            store.set_done(job_id, result)
        games = client.get("/api/players/alice/stats").json()["games"]
    assert [(g["id"], g["acpl"]) for g in games] == [("new", 10)]


def test_puzzles_from_critical_moments(tmp_path):
    pgn = '[White "alice"]\n[Black "bob"]\n[Date "2026.10.01"]\n\n' + CRITICAL_PGN
    with make_client(tmp_path, evals=CRITICAL_EVALS) as client:
        job = wait_for(
            client, client.post("/api/analyses", json={"pgn": pgn}).json()["id"]
        )
        stats = client.get("/api/players/bob/stats").json()

    nc6 = job["result"]["moves"][3]
    assert (nc6["critical"], nc6["second_san"], nc6["second_eval"]) == (True, "d6", 330)
    assert stats["critical"] == {"total": 2, "found": 1}
    # Only the move bob missed is a puzzle: the one he found is not.
    assert [(p["label"], p["kind"]) for p in stats["puzzles"]] == [
        ("3...Bc5", "critical")
    ]
    assert stats["puzzles"][0]["id"] == job["id"]
    assert stats["puzzles"][0]["line"] == ["g8f6"]


def test_puzzle_trainer(tmp_path):
    pgn = '[White "alice"]\n[Black "bob"]\n[Date "2026.10.01"]\n\n' + CRITICAL_PGN
    with make_client(tmp_path, evals=CRITICAL_EVALS) as client:
        job = wait_for(
            client, client.post("/api/analyses", json={"pgn": pgn}).json()["id"]
        )
        today = time.strftime("%Y-%m-%d", time.gmtime())
        url = f"/api/players/bob/puzzles?today={today}"
        day = client.get(url).json()
        assert (day["due"], day["new"], day["total"]) == (0, 1, 1)
        [puzzle] = day["puzzles"]
        assert (puzzle["id"], puzzle["ply"], puzzle["kind"]) == (
            job["id"],
            6,
            "critical",
        )
        assert puzzle["solution"][0]["san"] == "Nf6"
        assert puzzle["review"] is None
        assert client.get(url + "&summary=true").json()["puzzles"] == []

        # Failed today: due tomorrow, so nothing more today.
        response = client.post(
            f"/api/players/bob/puzzles/{puzzle['key']}/review",
            json={"solved": False, "today": today},
        )
        assert response.json()["step"] == 0
        day = client.get(url).json()
        assert (day["due"], day["new"], day["done_today"], day["tomorrow"]) == (
            0,
            0,
            1,
            1,
        )
        assert (
            client.post(
                "/api/players/bob/puzzles/nope/review", json={"solved": True}
            ).status_code
            == 404
        )

        single = client.get(f"/api/analyses/{job['id']}/puzzles/6").json()
        assert single["key"] == puzzle["key"]
        assert client.get(f"/api/analyses/{job['id']}/puzzles/1").status_code == 200
        assert client.get(f"/api/analyses/{job['id']}/puzzles/99").status_code == 404
        assert client.get("/api/analyses/nope/puzzles/1").status_code == 404


def test_peer_comparison(tmp_path, scholars_mate_pgn):
    def game(white, black, url):
        return {
            "url": url,
            "pgn": scholars_mate_pgn,
            "time_control": "600",
            "time_class": "rapid",
            "rules": "chess",
            "end_time": 1790000000,
            "white": {"username": white[0], "rating": white[1], "result": "win"},
            "black": {"username": black[0], "rating": black[1], "result": "resigned"},
        }

    chesscom = MagicMock()
    chesscom.get_recent_games.return_value = [
        game(("zed", 1480), ("Alice", 1500), "https://www.chess.com/game/live/9")
    ]
    # Alice's own game against bob is the one in the PGN, with this link.
    chesscom.get_monthly_games.return_value = [
        game(("Alice", 1500), ("bob", 1450), "https://www.chess.com/game/live/1")
    ]
    with make_client(tmp_path, chesscom=chesscom) as client:
        before = client.get("/api/players/zed/peers").json()
        found = client.post("/api/players/zed/peers").json()
        job_id = client.post(
            "/api/analyses", json={"pgn": found["to_analyse"][0]["pgn"]}
        ).json()["id"]
        wait_for(client, job_id)
        after = client.get("/api/players/zed/peers?time_control=600").json()
        higher = client.get("/api/players/zed/peers?offset=200").json()

    assert before["sample"] is None
    assert (before["time_control"], before["rating"]) == ("600", 1480)
    assert before["time_controls"][0]["games"] == 1
    assert chesscom.get_monthly_games.call_args_list[0].args == ("Alice", 2026, 9)
    assert found["sample"]["games"] == 1
    assert found["sample"]["sides"] == 2
    assert found["sample"]["analysed_sides"] == 0
    assert (found["sample"]["target"], found["sample"]["window"]) == (1480, 100)
    assert found["you"] == {"games": 1, "analysed": 0}
    assert after["sample"]["analysed_sides"] == 2
    assert after["to_analyse"] == []
    blunders = next(m for m in after["metrics"] if m["key"] == "blunders")
    assert blunders["peers"]["n"] > 0 and blunders["you"] is None
    # Samples of stronger players are kept apart.
    assert higher["sample"] is None


def test_move_explanations(tmp_path, scholars_mate_pgn):
    coach = MagicMock()
    coach.reword.return_value = "Nf6 lets the queen take f7 with mate."
    app = create_app(
        Settings(data_dir=tmp_path),
        engine_factory=lambda: QuittableEngine(EVALS),
        opening_factory=lambda: None,
        chesscom=MagicMock(),
        lichess=MagicMock(),
        coach=coach,
    )
    with TestClient(app) as client:
        assert client.get("/api/config").json()["coach"] is True
        job_id = client.post("/api/analyses", json={"pgn": scholars_mate_pgn}).json()[
            "id"
        ]
        wait_for(client, job_id)
        url = f"/api/analyses/{job_id}/explanations"

        plain = client.get(f"{url}/6").json()
        assert plain["coach"] is False
        assert plain["text"].startswith("3...Nf6 is a blunder")
        assert "Better was g6" in plain["text"]
        assert "g6" not in client.get(f"{url}/6?hide_best=true").json()["text"]

        worded = client.get(f"{url}/6?coach=true").json()
        assert worded == {**plain, "text": coach.reword.return_value, "coach": True}
        # The coach's answer is stored: the model is asked once.
        client.get(f"{url}/6?coach=true")
        assert coach.reword.call_count == 1

        assert client.get(f"{url}/99").status_code == 404
        assert client.get("/api/analyses/nope/explanations/1").status_code == 404
