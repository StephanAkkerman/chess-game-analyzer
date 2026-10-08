"""FastAPI app serving the API and the mobile web front end.

Run it with ``chess-analyzer-web`` or
``uvicorn --factory chess_analyzer.web.app:create_app``.
"""

from __future__ import annotations

import logging
import mimetypes
import secrets
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import chess
import chess.svg
import requests
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from chess_analyzer import __version__
from chess_analyzer.engine import find_engine
from chess_analyzer.fetch import ChessComClient, LichessClient
from chess_analyzer.peers import (
    RATING_WINDOW,
    collect_peer_games,
    compare,
    current_rating,
    engine_profile,
    own_profiles,
    peer_insights,
    summarise,
    time_controls,
)
from chess_analyzer.stats import game_record, player_stats
from chess_analyzer.web.device import InvalidEvaluation, find_device_engine
from chess_analyzer.web.service import (
    AnalysisService,
    EngineFactory,
    InvalidGame,
    OpeningFactory,
    QueueFull,
    TablebaseFactory,
    has_tablebases,
)
from chess_analyzer.web.settings import Settings
from chess_analyzer.web.store import DEVICE, DONE, FAILED, Job, Store

STATIC_DIR = Path(__file__).parent / "static"
DRAW_RESULTS = {
    "agreed",
    "repetition",
    "stalemate",
    "insufficient",
    "50move",
    "timevsinsufficient",
}
GAMES_CACHE_SECONDS = 120
SITE_NAMES = {"chess.com": "Chess.com", "lichess": "Lichess"}
# Months of the player's own games to compare with their peers, and the most
# unanalysed peer games to offer for analysis at once.
PEER_MONTHS = 3
PEER_BATCH = 10
# Older Pythons do not know WebAssembly, which browsers need to stream it.
mimetypes.add_type("application/wasm", ".wasm")


class AnalysisRequest(BaseModel):
    pgn: str
    # "device": Stockfish runs in the browser, "server": on this machine.
    engine: str = Field("server", pattern="^(server|device)$")


class DeviceEvaluation(BaseModel):
    id: str = Field(max_length=40)
    best: str = Field(max_length=5)
    cp: int | None = None
    mate: int | None = None


class EvaluationsRequest(BaseModel):
    evaluations: list[DeviceEvaluation] = Field(max_length=1000)
    # The engine's UCI name, e.g. "Stockfish 19 Lite WASM".
    engine: str | None = Field(None, max_length=80)


class EngineFiles(StaticFiles):
    """Stockfish.js builds. Their names carry the version, so they are cached
    for a long time."""

    async def get_response(self, path: str, scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=2592000"
        return response


def game_summary(raw: dict, username: str) -> dict:
    """Describe a game from ``username``'s point of view.

    ``raw`` is a Chess.com game object, or a Lichess game in the same shape
    (see :func:`chess_analyzer.fetch.lichess_game`).
    """
    white, black = raw.get("white", {}), raw.get("black", {})
    is_white = white.get("username", "").lower() == username.lower()
    me, opponent = (white, black) if is_white else (black, white)
    result = me.get("result", "")
    if result == "win":
        outcome = "win"
    elif result in DRAW_RESULTS:
        outcome = "draw"
    else:
        outcome = "loss"
    return {
        "url": raw.get("url"),
        "end_time": raw.get("end_time"),
        "time_class": raw.get("time_class"),
        "time_control": raw.get("time_control"),
        "rated": raw.get("rated", False),
        "color": "white" if is_white else "black",
        "rating": me.get("rating"),
        "opponent": {
            "username": opponent.get("username"),
            "rating": opponent.get("rating"),
        },
        "result": outcome,
        "pgn": raw.get("pgn", ""),
    }


def create_app(
    settings: Settings | None = None,
    engine_factory: EngineFactory | None = None,
    opening_factory: OpeningFactory | None = None,
    chesscom: ChessComClient | None = None,
    tablebase_factory: TablebaseFactory | None = None,
    lichess: LichessClient | None = None,
) -> FastAPI:
    """Build the app.

    The factories and clients can be replaced, which the tests use to avoid
    running Stockfish or calling external APIs.
    """
    settings = settings or Settings.from_env()
    chesscom = chesscom or ChessComClient()
    lichess = lichess or LichessClient(token=settings.lichess_token)
    clients = {"chess.com": chesscom, "lichess": lichess}
    device_engine = find_device_engine(settings.browser_engine_dir)
    server_engine = engine_factory is not None or bool(
        find_engine(settings.stockfish_path)
    )
    games_cache: dict[tuple[str, str, int], tuple[float, list[dict]]] = {}
    cache_lock = threading.Lock()
    # One peer search at a time: Chess.com asks clients not to send requests
    # in parallel.
    peers_lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = Store(settings.data_dir / "analyses.db")
        service = AnalysisService(
            settings, store, engine_factory, opening_factory, tablebase_factory
        )
        service.start()
        app.state.store = store
        app.state.service = service
        try:
            yield
        finally:
            service.stop()
            store.close()

    app = FastAPI(title="Chess Game Analyzer", version=__version__, lifespan=lifespan)

    @app.middleware("http")
    async def isolate(request: Request, call_next):
        """Make the page cross-origin isolated.

        Browsers only give WebAssembly several threads (SharedArrayBuffer)
        on such pages. Everything the app loads comes from its own origin, so
        this costs nothing.
        """
        response = await call_next(request)
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Embedder-Policy"] = "require-corp"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        return response

    def require_access(x_access_code: str | None = Header(default=None)) -> None:
        if settings.access_code and not secrets.compare_digest(
            (x_access_code or "").encode(), settings.access_code.encode()
        ):
            raise HTTPException(401, "A valid access code is required.")

    def job_to_dict(job: Job) -> dict:
        data = {
            "id": job.id,
            "status": job.status,
            "done": job.done,
            "total": job.total,
            "queue_position": app.state.service.queue_position(job.id),
        }
        if job.status == DONE:
            data["result"] = job.result
        if job.status == FAILED:
            data["error"] = job.error
        if job.status == DEVICE:
            limit = settings.limit
            data["pgn"] = job.pgn
            data["searches"] = job.state["searches"]
            data["limit"] = {
                "depth": limit.depth,
                "movetime": round(limit.time * 1000) if limit.time else None,
            }
        return data

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/config")
    def config() -> dict:
        return {
            "version": __version__,
            "access_code_required": bool(settings.access_code),
            "engine": settings.engine_label,
            "engine_name": app.state.service.engine_name,
            "opening_source": settings.opening_label,
            "tablebases": has_tablebases(settings.syzygy_path),
            "server_engine": server_engine,
            "device_engine": device_engine
            and {
                kind: f"/engine/{name}" if name else None
                for kind, name in device_engine.items()
            },
        }

    @app.get("/api/access", dependencies=[Depends(require_access)])
    def check_access() -> dict:
        return {"ok": True}

    def recent_games(username: str, months: int, site: str = "chess.com") -> list[dict]:
        key = (site, username.lower(), months)
        with cache_lock:
            cached = games_cache.get(key)
        if cached and time.monotonic() - cached[0] < GAMES_CACHE_SECONDS:
            return cached[1]
        name = SITE_NAMES[site]
        try:
            games = clients[site].get_recent_games(username, months=months)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status == 404:
                raise HTTPException(404, f"{name} player '{username}' not found.")
            if status == 429:
                raise HTTPException(
                    503, f"{name} asked us to slow down. Try again in a minute."
                )
            raise HTTPException(502, f"{name} did not respond correctly.")
        except requests.RequestException:
            raise HTTPException(502, f"Could not reach {name}.")
        with cache_lock:
            games_cache[key] = (time.monotonic(), games)
        return games

    @app.get("/api/players/{username}/games", dependencies=[Depends(require_access)])
    def player_games(
        username: str,
        months: int = Query(1, ge=1, le=12),
        limit: int = Query(30, ge=1, le=100),
        site: str = Query("chess.com", pattern="^(chess\\.com|lichess)$"),
    ) -> dict:
        games = recent_games(username, months, site)
        return {
            "username": username,
            "site": site,
            "games": [game_summary(g, username) for g in games[:limit]],
        }

    def peer_report(
        username: str, time_control: str | None, offset: int, collect: bool
    ) -> dict:
        games = recent_games(username, PEER_MONTHS)
        controls = time_controls(games)
        if time_control is None:
            if not controls:
                raise HTTPException(404, "No recent games to compare.")
            time_control = controls[0]["time_control"]
        rating = current_rating(games, username, time_control)
        if collect:
            if rating is None:
                raise HTTPException(422, "No recent games of this time control.")
            target = rating + offset
            with peers_lock:
                peer_games = collect_peer_games(
                    chesscom, username, games, time_control, target
                )
            app.state.store.save_peer_sample(
                username,
                time_control,
                offset,
                {
                    "rating": rating,
                    "target": target,
                    "window": RATING_WINDOW,
                    "games": peer_games,
                },
            )
        sample = app.state.store.peer_sample(username, time_control, offset)

        name = username.lower()
        own_engine = []
        for _, result in app.state.store.results_for_player(username):
            headers = result.get("headers", {})
            if headers.get("TimeControl") != time_control:
                continue
            color = "white" if headers.get("White", "").lower() == name else "black"
            own_engine.append(engine_profile(result, color))
        own = own_profiles(games, username, time_control)
        you = summarise(own, own_engine)

        peer_games = sample["games"] if sample else []
        analysed = app.state.store.results_for_links([g["url"] for g in peer_games])
        profiles, engine, to_analyse = [], [], []
        for game in peer_games:
            profiles.extend(game["sides"].values())
            if game["url"] in analysed:
                result = analysed[game["url"]][1]
                engine.extend(engine_profile(result, c) for c in game["sides"])
            else:
                to_analyse.append(game)
        # Games where both players are peers count twice.
        to_analyse.sort(key=lambda g: -len(g["sides"]))
        rows = compare(you, summarise(profiles, engine))
        report = {
            "username": username,
            "time_control": time_control,
            "time_controls": controls,
            "offset": offset,
            "rating": rating,
            "you": {"games": len(own), "analysed": len(own_engine)},
            "metrics": rows,
            "sample": None,
            "insights": [],
            "to_analyse": [],
        }
        if sample:
            report["sample"] = {
                "rating": sample["rating"],
                "target": sample["target"],
                "window": sample["window"],
                "created_at": sample["created_at"],
                "games": len(peer_games),
                "sides": len(profiles),
                "analysed_games": len(peer_games) - len(to_analyse),
                "analysed_sides": len(engine),
            }
            report["insights"] = peer_insights(rows, sample["target"], rating)
            report["to_analyse"] = [
                {"url": g["url"], "pgn": g["pgn"]} for g in to_analyse[:PEER_BATCH]
            ]
        return report

    @app.get("/api/players/{username}/peers", dependencies=[Depends(require_access)])
    def player_peers(
        username: str,
        time_control: str | None = Query(None, max_length=20),
        offset: int = Query(0, ge=-500, le=500),
    ) -> dict:
        """The player compared with the peer games found earlier."""
        return peer_report(username, time_control, offset, collect=False)

    @app.post("/api/players/{username}/peers", dependencies=[Depends(require_access)])
    def find_peers(
        username: str,
        time_control: str | None = Query(None, max_length=20),
        offset: int = Query(0, ge=-500, le=500),
    ) -> dict:
        """Look for games of players at the player's rating on Chess.com."""
        return peer_report(username, time_control, offset, collect=True)

    @app.get("/api/players/{username}/stats", dependencies=[Depends(require_access)])
    def player_statistics(username: str) -> dict:
        """Trends and weaknesses across the player's analysed games."""
        records = [
            record
            for job_id, result in app.state.store.results_for_player(username)
            if (record := game_record(result, username, job_id))
        ]
        return {"username": username, **player_stats(records)}

    @app.post("/api/analyses", status_code=202, dependencies=[Depends(require_access)])
    def create_analysis(request: AnalysisRequest) -> dict:
        device = request.engine == "device"
        if device and device_engine is None:
            raise HTTPException(422, "This server has no engine for the browser.")
        try:
            job = app.state.service.submit(request.pgn, device=device)
        except InvalidGame as exc:
            raise HTTPException(422, str(exc))
        except QueueFull as exc:
            raise HTTPException(503, str(exc))
        return job_to_dict(job)

    @app.post(
        "/api/analyses/{job_id}/evaluations", dependencies=[Depends(require_access)]
    )
    def add_evaluations(job_id: str, request: EvaluationsRequest) -> dict:
        """Evaluations of positions the browser searched for a device analysis."""
        try:
            job = app.state.service.submit_evaluations(
                job_id,
                [e.model_dump() for e in request.evaluations],
                engine_name=request.engine,
            )
        except InvalidEvaluation as exc:
            raise HTTPException(422, str(exc))
        if job is None:
            raise HTTPException(404, "Analysis not found.")
        return job_to_dict(job)

    @app.get("/api/analyses/{job_id}")
    def get_analysis(job_id: str) -> dict:
        job = app.state.store.get(job_id)
        if job is None:
            raise HTTPException(404, "Analysis not found.")
        return job_to_dict(job)

    @app.get("/api/pieces/{name}.svg")
    def piece(name: str) -> Response:
        if len(name) != 2 or name[0] not in "wb" or name[1] not in "KQRBNP":
            raise HTTPException(404, "Unknown piece.")
        symbol = name[1] if name[0] == "w" else name[1].lower()
        svg = chess.svg.piece(chess.Piece.from_symbol(symbol))
        return Response(
            svg,
            media_type="image/svg+xml",
            headers={"Cache-Control": "public, max-age=604800, immutable"},
        )

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def manifest() -> FileResponse:
        return FileResponse(
            STATIC_DIR / "manifest.webmanifest",
            media_type="application/manifest+json",
        )

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    if device_engine is not None:
        app.mount(
            "/engine", EngineFiles(directory=settings.browser_engine_dir), name="engine"
        )
    return app


def main() -> None:
    """Run the web app with uvicorn (``chess-analyzer-web``)."""
    import argparse
    import os

    import uvicorn

    parser = argparse.ArgumentParser(description="Run the Chess Game Analyzer web app.")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_app(), host=args.host, port=args.port, proxy_headers=True)


if __name__ == "__main__":
    main()
