"""Puzzles from the moves a player did not find, practised with spaced repetition.

A puzzle is the position just before one of the player's mistakes, misses or
blunders, or a critical moment they got wrong. The solution is the move they
did not play, followed by the engine's line for as long as it stays forcing,
so a puzzle can take several moves to solve.

Puzzles come back on a schedule, as flashcards do in Anki: one solved comes
back after 3 days, then after a week, two weeks and so on; one failed comes
back the next day and starts over. That way practice goes to the positions
the player keeps getting wrong.
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta

import chess

from chess_analyzer.engine import ERRORS, FOUND

# The most moves of the solver in a solution.
SOLUTION_MOVES = 3
# Game moves shown before the puzzle position, to practise seeing it coming:
# two moves of each side.
LEAD_IN_PLIES = 4
# Days until a puzzle comes back, by how many times in a row it was solved.
# A failed puzzle starts over at the first step: tomorrow.
INTERVALS = (1, 3, 7, 14, 30, 60, 120)
# Puzzles solved this many times in a row count as learned.
LEARNED_STEP = 3
# The most puzzles a day that were never seen before.
NEW_PER_DAY = 10


def position_key(fen: str) -> str:
    """Identify a puzzle by its position, leaving out the move counters.

    The same position reached in two games is one puzzle.
    """
    position = " ".join(fen.split()[:4])
    return hashlib.sha256(position.encode()).hexdigest()[:16]


def _forcing(board: chess.Board, move: chess.Move) -> bool:
    return (
        board.is_capture(move) or board.gives_check(move) or move.promotion is not None
    )


def solution_line(board: chess.Board, line: list[str]) -> list[chess.Move]:
    """Cut the engine's ``line`` from ``board`` down to a puzzle's solution.

    The solution ends with a move of the solver, after at most
    :data:`SOLUTION_MOVES` of them, at checkmate or at the last capture,
    check or promotion: the quiet moves after the point of a tactic are not
    part of the puzzle. The first move is always kept.
    """
    board = board.copy(stack=False)
    moves: list[chess.Move] = []
    forcing: list[bool] = []
    for uci in line[: 2 * SOLUTION_MOVES - 1]:
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            break
        if not board.is_legal(move):
            break
        forcing.append(_forcing(board, move))
        moves.append(move)
        board.push(move)
        if board.is_game_over():
            break
    if len(moves) % 2 == 0 and moves:
        moves.pop()
        forcing.pop()
    while len(moves) > 1 and not forcing[len(moves) - 1]:
        del moves[-2:]
        del forcing[-2:]
    return moves


def candidates(result: dict, color: str) -> list[dict]:
    """List the positions of one game that make puzzles for ``color``.

    ``result`` comes from :func:`chess_analyzer.serialize.result_to_dict`.
    Those are the positions before ``color``'s mistakes, misses and blunders,
    and the critical moments where ``color`` did not find the only move.
    Moves the player found are left out: there is nothing to practise.
    """
    found = []
    for m in result.get("moves", []):
        if m["color"] != color or not m.get("best_uci") or m["uci"] == m["best_uci"]:
            continue
        missed_critical = m.get("critical") and m["classification"] not in FOUND
        if not missed_critical and m["classification"] not in ERRORS:
            continue
        found.append(
            {
                "key": position_key(m["fen_before"]),
                "ply": m["ply"],
                "label": m["label"],
                "fen": m["fen_before"],
                "best_san": m["best_san"],
                "best_uci": m["best_uci"],
                "line": m.get("best_line") or [m["best_uci"]],
                "kind": "critical" if missed_critical else m["classification"],
                "category": m.get("category", ""),
            }
        )
    return found


def _step_moves(board: chess.Board) -> dict:
    """The legal moves of the solver (UCI to SAN), and those that mate."""
    legal, mates = {}, []
    for move in board.legal_moves:
        legal[move.uci()] = board.san(move)
        board.push(move)
        if board.is_checkmate():
            mates.append(move.uci())
        board.pop()
    return {"legal": legal, "mates": mates}


def build_puzzle(result: dict, ply: int, analysis_id: str | None = None) -> dict | None:
    """Describe the puzzle before move ``ply`` of an analysed game.

    The front end needs no chess rules of its own: for every move the solver
    has to find, the puzzle lists the legal moves and those that mate, and
    for every move of the solution the position after it. ``lead_in`` holds
    the game moves before the puzzle (:data:`LEAD_IN_PLIES`), to show how the
    position came about. Returns ``None`` if that move has no best move.
    """
    moves = result.get("moves", [])
    if not 0 < ply <= len(moves):
        return None
    m = moves[ply - 1]
    if not m.get("best_uci"):
        return None
    board = chess.Board(m["fen_before"])
    line = solution_line(board, m.get("best_line") or [m["best_uci"]])
    if not line:
        return None
    solution, steps = [], []
    for i, move in enumerate(line):
        if i % 2 == 0:
            steps.append(_step_moves(board))
        san = board.san(move)
        board.push(move)
        solution.append({"uci": move.uci(), "san": san, "fen": board.fen()})

    start = max(0, ply - 1 - LEAD_IN_PLIES)
    lead_in = {
        "fen": moves[start - 1]["fen"] if start else result.get("start_fen"),
        "moves": [
            {"uci": g["uci"], "san": g["san"], "label": g["label"], "fen": g["fen"]}
            for g in moves[start : ply - 1]
        ],
    }
    headers = result.get("headers", {})
    color = m["color"]
    missed_critical = m.get("critical") and m["classification"] not in FOUND
    return {
        "key": position_key(m["fen_before"]),
        "id": analysis_id,
        "ply": ply,
        "color": color,
        "kind": "critical" if missed_critical else m["classification"],
        "category": m.get("category", ""),
        "played": {"san": m["san"], "label": m["label"], "uci": m["uci"]},
        "classification": m["classification"],
        "eval": m.get("eval_before"),
        "source": m.get("source_before"),
        "second_san": m.get("second_san", ""),
        "second_eval": m.get("second_eval"),
        "fen": m["fen_before"],
        "solution": solution,
        "steps": steps,
        "lead_in": lead_in,
        "white": headers.get("White"),
        "black": headers.get("Black"),
        "date": (headers.get("UTCDate") or headers.get("Date", "")).replace(".", "-"),
        "link": headers.get("Link"),
    }


def review(previous: dict | None, solved: bool, today: date) -> dict:
    """Schedule a puzzle after an attempt on ``today``.

    ``previous`` is the puzzle's last review, as this returns it, or ``None``
    for a new puzzle. Solving it moves it one step along :data:`INTERVALS`;
    failing it brings it back tomorrow and starts over. Solving a puzzle
    again before it is due does not move it along: knowing it an hour later
    shows little.
    """
    today_iso = today.isoformat()
    data = dict(
        previous or {"step": 0, "due": today_iso, "seen": 0, "solved": 0, "failed": 0}
    )
    data.setdefault("first", today_iso)
    data["seen"] += 1
    data["last"] = today_iso
    if solved:
        data["solved"] += 1
        if previous is not None and previous["due"] > today_iso:
            return data
        data["step"] = min(data["step"] + 1, len(INTERVALS) - 1)
    else:
        data["failed"] += 1
        data["step"] = 0
    data["due"] = (today + timedelta(days=INTERVALS[data["step"]])).isoformat()
    return data


def unique(puzzles: list[dict]) -> list[dict]:
    """Keep the first puzzle of each position."""
    seen, kept = set(), []
    for p in puzzles:
        if p["key"] not in seen:
            seen.add(p["key"])
            kept.append(p)
    return kept


def daily_set(
    puzzles: list[dict],
    reviews: dict[str, dict],
    today: date,
    new_per_day: int = NEW_PER_DAY,
) -> dict:
    """Pick today's puzzles: those due, then some never seen before.

    ``puzzles`` are the player's puzzles, newest first, each with a ``key``;
    ``reviews`` the reviews by key. Due puzzles come first, the longest
    overdue first; new ones are the newest, up to ``new_per_day`` minus the
    new ones already started today.

    Returns ``due`` and ``new`` (lists of puzzles) and counts: ``total``,
    ``learned`` (solved :data:`LEARNED_STEP` times in a row),
    ``done_today`` and ``tomorrow`` (due by then, if today's are solved).
    """
    today_iso = today.isoformat()
    puzzles = unique(puzzles)
    known = [p for p in puzzles if p["key"] in reviews]
    due = sorted(
        (p for p in known if reviews[p["key"]]["due"] <= today_iso),
        key=lambda p: reviews[p["key"]]["due"],
    )
    started = sum(1 for r in reviews.values() if r.get("first") == today_iso)
    fresh = [p for p in puzzles if p["key"] not in reviews]
    new = fresh[: max(0, new_per_day - started)]
    tomorrow = (today + timedelta(days=1)).isoformat()
    return {
        "due": due,
        "new": new,
        "total": len(puzzles),
        "unseen": len(fresh),
        "learned": sum(1 for p in known if reviews[p["key"]]["step"] >= LEARNED_STEP),
        "done_today": sum(
            1 for p in known if reviews[p["key"]].get("last") == today_iso
        ),
        "tomorrow": sum(
            1 for p in known if today_iso < reviews[p["key"]]["due"] <= tomorrow
        ),
    }
