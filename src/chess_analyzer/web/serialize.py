"""Convert analysis results to JSON-friendly dictionaries."""

from __future__ import annotations

import chess
import chess.pgn

from chess_analyzer.engine import CLASSIFICATIONS, GameAnalysis, MoveAnalysis
from chess_analyzer.openings import OpeningDeviation
from chess_analyzer.parse import opening_name

HEADERS = (
    "Event",
    "Site",
    "Date",
    "White",
    "Black",
    "Result",
    "WhiteElo",
    "BlackElo",
    "TimeControl",
    "Termination",
    "ECO",
    "Link",
)


def _side(color: chess.Color) -> str:
    return "white" if color == chess.WHITE else "black"


def move_to_dict(move: MoveAnalysis) -> dict:
    return {
        "ply": move.ply,
        "color": _side(move.color),
        "label": move.label,
        "san": move.san,
        "uci": move.uci,
        "best_san": move.best_san,
        "best_uci": move.best_uci,
        "eval_before": move.eval_before,
        "eval_after": move.eval_after,
        "cp_loss": move.cp_loss,
        "classification": move.classification,
        "fen_before": move.fen_before,
        "fen": move.fen_after,
    }


def summary_to_dict(analysis: GameAnalysis, color: chess.Color) -> dict:
    counts = analysis.counts(color)
    return {
        "acpl": round(analysis.average_cp_loss(color)),
        "counts": {k: counts.get(k, 0) for k in CLASSIFICATIONS},
        "worst": [m.ply for m in analysis.worst_moves(color)],
    }


def deviation_to_dict(deviation: OpeningDeviation | None) -> dict | None:
    if deviation is None:
        return None
    return {
        "ply": deviation.ply,
        "color": _side(deviation.color),
        "label": deviation.label,
        "san": deviation.played_san,
        "alternatives": [
            {
                "san": m.san,
                "uci": m.uci,
                "games": m.games,
                "score": m.score_for(deviation.color),
            }
            for m in deviation.alternatives[:5]
        ],
    }


def result_to_dict(
    game: chess.pgn.Game,
    analysis: GameAnalysis,
    deviation: OpeningDeviation | None,
    opening_checked: bool,
    opening_error: str | None,
    engine_label: str,
) -> dict:
    return {
        "headers": {k: game.headers[k] for k in HEADERS if k in game.headers},
        "start_fen": game.board().fen(),
        "engine": engine_label,
        "opening": {
            "name": opening_name(game),
            "checked": opening_checked,
            "error": opening_error,
            "deviation": deviation_to_dict(deviation),
        },
        "moves": [move_to_dict(m) for m in analysis.moves],
        "summary": {
            "white": summary_to_dict(analysis, chess.WHITE),
            "black": summary_to_dict(analysis, chess.BLACK),
        },
    }
