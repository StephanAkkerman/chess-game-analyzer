"""FastAPI app serving the API and the mobile web front end.

Run it with ``chess-analyzer-web`` or
``uvicorn --factory chess_analyzer.web.app:create_app``.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import chess
import chess.svg
import requests
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from chess_analyzer import __version__
from chess_analyzer.fetch import ChessComClient
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
from chess_analyzer.web.store import DONE, FAILED, Job, Store

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


class AnalysisRequest(BaseModel):
    pgn: str


def game_summary(raw: dict, username: str) -> dict:
    """Describe a Chess.com game from ``username``'s point of view."""
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
) -> FastAPI:
    """Build the app.

    The factories and client can be replaced, which the tests use to avoid
    running Stockfish or calling external APIs.
    """
    settings = settings or Settings.from_env()
    chesscom = chesscom or ChessComClient()
    games_cache: dict[tuple[str, int], tuple[float, list[dict]]] = {}
    cache_lock = threading.Lock()

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
        }

    @app.get("/api/access", dependencies=[Depends(require_access)])
    def check_access() -> dict:
        return {"ok": True}

    @app.get("/api/players/{username}/games", dependencies=[Depends(require_access)])
    def player_games(
        username: str,
        months: int = Query(1, ge=1, le=12),
        limit: int = Query(30, ge=1, le=100),
    ) -> dict:
        key = (username.lower(), months)
        with cache_lock:
            cached = games_cache.get(key)
        if cached and time.monotonic() - cached[0] < GAMES_CACHE_SECONDS:
            games = cached[1]
        else:
            try:
                games = chesscom.get_recent_games(username, months=months)
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else 0
                if status == 404:
                    raise HTTPException(
                        404, f"Chess.com player '{username}' not found."
                    )
                raise HTTPException(502, "Chess.com did not respond correctly.")
            except requests.RequestException:
                raise HTTPException(502, "Could not reach Chess.com.")
            with cache_lock:
                games_cache[key] = (time.monotonic(), games)
        return {
            "username": username,
            "games": [game_summary(g, username) for g in games[:limit]],
        }

    @app.post("/api/analyses", status_code=202, dependencies=[Depends(require_access)])
    def create_analysis(request: AnalysisRequest) -> dict:
        try:
            job = app.state.service.submit(request.pgn)
        except InvalidGame as exc:
            raise HTTPException(422, str(exc))
        except QueueFull as exc:
            raise HTTPException(503, str(exc))
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
