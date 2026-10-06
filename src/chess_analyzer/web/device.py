"""Analysis with Stockfish running in the user's browser.

Only the engine searches move to the browser. The server still finds the book
moves, probes the endgame tablebases, classifies the moves and keeps the
result, so both kinds of analysis give the same report.

:func:`chess_analyzer.engine.analyze_game` runs as usual, with a
:class:`DeviceEngine` that answers searches from the evaluations the browser
sent. A search it has no answer for yet is recorded and answered with a
neutral placeholder, so the run completes; that run's result is thrown away
and the recorded searches go to the browser. Once they come back the game is
analysed again, until no search is missing. Searches that depend on earlier
results simply show up in a later round.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import chess
import chess.engine

from chess_analyzer.engine import MATE_THRESHOLD

# Longest mate the browser may report, in moves.
MAX_MATE = 500


class InvalidEvaluation(ValueError):
    """An evaluation from the browser does not fit its search."""


def search_id(board: chess.Board, root_moves: list[chess.Move] | None) -> str:
    """Identify a search of a position of the game being analysed.

    Positions are numbered by ply, which identifies them within one game.
    """
    ply = str(len(board.move_stack))
    if root_moves is None:
        return ply
    moves = ",".join(sorted(m.uci() for m in root_moves))
    return f"{ply}-{hashlib.sha256(moves.encode()).hexdigest()[:12]}"


def describe_search(board: chess.Board, root_moves: list[chess.Move] | None) -> dict:
    """Describe a search for the browser, as a UCI position and move list.

    The moves leading to the position are sent along, like python-chess does,
    so the engine can recognise repetitions.
    """
    return {
        "id": search_id(board, root_moves),
        "fen": board.root().fen(),
        "moves": [m.uci() for m in board.move_stack],
        "searchmoves": [m.uci() for m in root_moves] if root_moves else [],
    }


def parse_evaluation(search: dict, data: dict) -> dict:
    """Check an evaluation the browser sent for ``search``.

    ``data`` has the best move (``best``, UCI) and a score from the side to
    move's point of view: centipawns (``cp``) or moves to mate (``mate``).
    Returns the evaluation to store.
    """
    board = chess.Board(search["fen"])
    for uci in search["moves"]:
        board.push_uci(uci)
    try:
        best = chess.Move.from_uci(data.get("best") or "")
    except ValueError:
        raise InvalidEvaluation(f"Search {search['id']}: no valid best move.")
    allowed = search["searchmoves"] or [m.uci() for m in board.legal_moves]
    if best.uci() not in allowed:
        raise InvalidEvaluation(f"Search {search['id']}: {best} is not allowed.")
    cp, mate = data.get("cp"), data.get("mate")
    if mate is not None:
        if not isinstance(mate, int) or mate == 0 or abs(mate) > MAX_MATE:
            raise InvalidEvaluation(f"Search {search['id']}: invalid mate score.")
        return {"best": best.uci(), "mate": mate}
    if not isinstance(cp, int):
        raise InvalidEvaluation(f"Search {search['id']}: no score.")
    # Anything larger would read as a forced mate.
    limit = MATE_THRESHOLD - 1
    return {"best": best.uci(), "cp": max(-limit, min(limit, cp))}


class DeviceEngine:
    """Stands in for a UCI engine, with the evaluations the browser sent.

    Parameters
    ----------
    evaluations : dict
        Evaluations by search id, as returned by :func:`parse_evaluation`.

    Attributes
    ----------
    missing : dict
        Searches that were asked for but have no evaluation yet, by id, as
        :func:`describe_search` describes them.
    """

    def __init__(self, evaluations: dict[str, dict]) -> None:
        self.evaluations = evaluations
        self.missing: dict[str, dict] = {}

    def analyse(
        self,
        board: chess.Board,
        limit: chess.engine.Limit | None = None,
        root_moves: list[chess.Move] | None = None,
        **kwargs,
    ) -> dict:
        key = search_id(board, root_moves)
        found = self.evaluations.get(key)
        if found is None:
            self.missing.setdefault(key, describe_search(board, root_moves))
            # A neutral score and no best move: no move counts as an error
            # and nothing is searched again on the strength of it.
            return {"score": chess.engine.PovScore(chess.engine.Cp(0), chess.WHITE)}
        if "mate" in found:
            score: chess.engine.Score = chess.engine.Mate(found["mate"])
        else:
            score = chess.engine.Cp(found["cp"])
        return {
            "score": chess.engine.PovScore(score, board.turn),
            "pv": [chess.Move.from_uci(found["best"])],
        }


def find_device_engine(directory: str | Path | None) -> dict | None:
    """Find the Stockfish.js builds the browser can load from ``directory``.

    Each build is a ``.js`` file with a ``.wasm`` file of the same name next
    to it. Builds with ``single`` in the name run on one thread and work in
    every browser; the others use several threads, which needs a page that
    is cross-origin isolated. Returns the file names of both kinds (``None``
    if missing), or ``None`` if there is no build at all.
    """
    if not directory or not Path(directory).is_dir():
        return None
    builds = sorted(
        path.name
        for path in Path(directory).glob("stockfish*.js")
        if path.with_suffix(".wasm").is_file()
    )
    single = next((name for name in builds if "single" in name), None)
    threaded = next((name for name in builds if "single" not in name), None)
    if single is None and threaded is None:
        return None
    return {"single": single, "threaded": threaded}
