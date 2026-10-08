import chess
import pytest
from test_engine import EVALS, FakeEngine
from test_openings import FakeSource
from test_web import make_client

from chess_analyzer.engine import MATE_SCORE, analyze_game
from chess_analyzer.parse import read_games
from chess_analyzer.web.device import (
    DeviceEngine,
    InvalidEvaluation,
    describe_search,
    find_device_engine,
    parse_evaluation,
)


def answer(search):
    """What the browser would send for ``search``, from the EVALS table.

    EVALS scores are from White's point of view; UCI scores are from the side
    to move's. Like FakeEngine, a search without the best move finds another
    move that scores as well.
    """
    ply = len(search["moves"])
    score, best = EVALS[ply]
    if search["searchmoves"]:
        best = search["searchmoves"][0]
    sign = 1 if ply % 2 == 0 else -1
    if abs(score) >= MATE_SCORE - 500:
        return {"id": search["id"], "best": best, "mate": sign * (MATE_SCORE - score)}
    return {"id": search["id"], "best": best, "cp": sign * score}


def test_device_engine_matches_a_real_engine(scholars_mate_pgn):
    game = read_games(scholars_mate_pgn)[0]
    expected = analyze_game(game, FakeEngine(EVALS))

    evaluations, rounds = {}, []
    while True:
        engine = DeviceEngine(evaluations)
        analysis = analyze_game(game, engine)
        if not engine.missing:
            break
        rounds.append(list(engine.missing.values()))
        for s in rounds[-1]:
            evaluations[s["id"]] = parse_evaluation(s, answer(s))
    # First every position but the final checkmate, then the second-best
    # moves of the positions that may be critical moments.
    assert sorted(len(s["moves"]) for s in rounds[0]) == list(range(7))
    assert not any(s["searchmoves"] for s in rounds[0])
    assert len(rounds) == 2
    assert all(s["searchmoves"] for s in rounds[1])
    assert analysis == expected


def test_placeholder_run_finds_no_errors(scholars_mate_pgn):
    game = read_games(scholars_mate_pgn)[0]
    analysis = analyze_game(game, DeviceEngine({}))
    assert {m.classification for m in analysis.moves} <= {"good", "best"}
    assert not any(m.category for m in analysis.moves)


def test_searches_among_some_moves_are_separate():
    board = chess.Board()
    board.push_uci("e2e4")
    engine = DeviceEngine({})
    engine.analyse(board, None)
    engine.analyse(board, None, root_moves=[chess.Move.from_uci("e7e5")])
    full, restricted = engine.missing.values()
    assert full["id"] != restricted["id"]
    assert full["moves"] == restricted["moves"] == ["e2e4"]
    assert (full["searchmoves"], restricted["searchmoves"]) == ([], ["e7e5"])

    evaluation = parse_evaluation(restricted, {"best": "e7e5", "cp": 20})
    engine = DeviceEngine({restricted["id"]: evaluation})
    info = engine.analyse(board, None, root_moves=[chess.Move.from_uci("e7e5")])
    assert info["score"].white().score() == -20
    assert info["pv"] == [chess.Move.from_uci("e7e5")]


@pytest.mark.parametrize(
    "data",
    [
        {"best": "e2e5", "cp": 0},  # illegal
        {"best": "d7d5", "cp": 0},  # not among the search moves
        {"best": "", "cp": 0},
        {"best": "e7e5"},
        {"best": "e7e5", "mate": 0},
    ],
)
def test_invalid_evaluations_are_rejected(data):
    board = chess.Board()
    board.push_uci("e2e4")
    search = describe_search(board, [chess.Move.from_uci("e7e5")])
    with pytest.raises(InvalidEvaluation):
        parse_evaluation(search, data)


def test_huge_scores_are_not_mates():
    search = describe_search(chess.Board(), None)
    assert parse_evaluation(search, {"best": "e2e4", "cp": 99_999})["cp"] < 9_500


def test_find_device_engine(tmp_path):
    assert find_device_engine(tmp_path) is None
    assert find_device_engine(None) is None
    for name in ("stockfish-19-lite-single", "stockfish-19-lite"):
        (tmp_path / f"{name}.js").write_text("")
        (tmp_path / f"{name}.wasm").write_text("")
    (tmp_path / "stockfish-19-asm.js").write_text("")  # no .wasm: not a build
    assert find_device_engine(tmp_path) == {
        "single": "stockfish-19-lite-single.js",
        "threaded": "stockfish-19-lite.js",
    }


@pytest.fixture
def engine_dir(tmp_path):
    directory = tmp_path / "engine"
    directory.mkdir()
    (directory / "stockfish-19-lite-single.js").write_text("// engine")
    (directory / "stockfish-19-lite-single.wasm").write_bytes(b"\0asm")
    return directory


def test_device_analysis_round_trip(tmp_path, engine_dir, scholars_mate_pgn):
    source = FakeSource(["e4 e5 Bc4 Nc6 Qh5 g6"])
    with make_client(
        tmp_path, opening=source, browser_engine_dir=str(engine_dir)
    ) as client:
        config = client.get("/api/config").json()
        assert config["device_engine"] == {
            "single": "/engine/stockfish-19-lite-single.js",
            "threaded": None,
        }
        wasm = client.get("/engine/stockfish-19-lite-single.wasm")
        assert wasm.headers["content-type"] == "application/wasm"
        assert "max-age" in wasm.headers["cache-control"]
        assert wasm.headers["cross-origin-embedder-policy"] == "require-corp"

        response = client.post(
            "/api/analyses", json={"pgn": scholars_mate_pgn, "engine": "device"}
        )
        assert response.status_code == 202
        job = response.json()
        assert job["status"] == "device"
        assert job["limit"] == {"depth": 16, "movetime": 15000}
        # Book moves and the final checkmate need no search.
        assert sorted(len(s["moves"]) for s in job["searches"]) == [5, 6]
        assert (job["done"], job["total"]) == (0, 2)

        # Answers arrive in parts; unknown searches are ignored.
        first, second = job["searches"]
        url = f"/api/analyses/{job['id']}/evaluations"
        job = client.post(
            url, json={"evaluations": [answer(first), {**answer(first), "id": "99"}]}
        ).json()
        assert (job["status"], job["done"], job["total"]) == ("device", 1, 2)
        assert [s["id"] for s in job["searches"]] == [second["id"]]

        job = client.post(
            url,
            json={"evaluations": [answer(second)], "engine": "Stockfish 19 Lite WASM"},
        ).json()
        # Then the searches for critical moments, if any.
        while job["status"] == "device":
            job = client.post(
                url, json={"evaluations": [answer(s) for s in job["searches"]]}
            ).json()
        assert job["status"] == "done"
        assert "searches" not in job

        # The same game again gives the same analysis.
        again = client.post(
            "/api/analyses", json={"pgn": scholars_mate_pgn, "engine": "device"}
        ).json()
        assert again["id"] == job["id"]

    result = job["result"]
    assert result["engine_name"] == "Stockfish 19 Lite WASM"
    assert result["engine"].endswith("on your device")
    assert result["positions"] == {"book": 5, "engine": 2, "terminal": 1}
    assert result["opening"]["deviation"]["label"] == "3...Nf6"
    nf6 = result["moves"][5]
    assert (nf6["classification"], nf6["category"]) == ("blunder", "allowed_mate")


def test_device_analysis_survives_a_restart(tmp_path, engine_dir, scholars_mate_pgn):
    settings = {"browser_engine_dir": str(engine_dir)}
    pgn = {"pgn": scholars_mate_pgn, "engine": "device"}
    with make_client(tmp_path, **settings) as client:
        job = client.post("/api/analyses", json=pgn).json()
    with make_client(tmp_path, **settings) as client:
        assert client.get(f"/api/analyses/{job['id']}").json()["status"] == "device"
        while job["status"] == "device":
            job = client.post(
                f"/api/analyses/{job['id']}/evaluations",
                json={"evaluations": [answer(s) for s in job["searches"]]},
            ).json()
    assert job["status"] == "done"


def test_finished_server_analysis_is_reused(tmp_path, engine_dir, scholars_mate_pgn):
    from test_web import wait_for

    with make_client(tmp_path, browser_engine_dir=str(engine_dir)) as client:
        server = client.post("/api/analyses", json={"pgn": scholars_mate_pgn}).json()
        wait_for(client, server["id"])
        device = client.post(
            "/api/analyses", json={"pgn": scholars_mate_pgn, "engine": "device"}
        ).json()
    assert device["id"] == server["id"]


def test_invalid_evaluation_is_422(tmp_path, engine_dir, scholars_mate_pgn):
    with make_client(tmp_path, browser_engine_dir=str(engine_dir)) as client:
        job = client.post(
            "/api/analyses", json={"pgn": scholars_mate_pgn, "engine": "device"}
        ).json()
        search = job["searches"][0]
        response = client.post(
            f"/api/analyses/{job['id']}/evaluations",
            json={"evaluations": [{"id": search["id"], "best": "a1a8", "cp": 0}]},
        )
        assert response.status_code == 422
        assert (
            client.post(
                "/api/analyses/nope/evaluations", json={"evaluations": []}
            ).status_code
            == 404
        )


def test_device_analysis_needs_engine_files(tmp_path, scholars_mate_pgn):
    with make_client(tmp_path) as client:
        assert client.get("/api/config").json()["device_engine"] is None
        response = client.post(
            "/api/analyses", json={"pgn": scholars_mate_pgn, "engine": "device"}
        )
    assert response.status_code == 422


def test_engine_line_is_kept_up_to_its_first_illegal_move():
    search = describe_search(chess.Board(), None)
    data = {"best": "e2e4", "cp": 20, "pv": ["e2e4", "e7e5", "e2e4", "g1f3"]}
    assert parse_evaluation(search, data)["pv"] == ["e2e4", "e7e5"]
    # A line that does not start with the best move is left out.
    data = {"best": "e2e4", "cp": 20, "pv": ["d2d4", "d7d5"]}
    assert parse_evaluation(search, data)["pv"] == ["e2e4"]
    assert parse_evaluation(search, {"best": "e2e4", "cp": 20})["pv"] == ["e2e4"]

    engine = DeviceEngine(
        {search["id"]: {"best": "e2e4", "cp": 20, "pv": ["e2e4", "e7e5"]}}
    )
    info = engine.analyse(chess.Board())
    assert [m.uci() for m in info["pv"]] == ["e2e4", "e7e5"]
    # Evaluations stored before lines were kept still work.
    engine = DeviceEngine({search["id"]: {"best": "e2e4", "cp": 20}})
    assert [m.uci() for m in engine.analyse(chess.Board())["pv"]] == ["e2e4"]
