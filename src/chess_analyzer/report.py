"""Turn analysis results into a plain-text report."""

from __future__ import annotations

import chess
import chess.pgn

from chess_analyzer.engine import (
    CLASSIFICATIONS,
    FOUND,
    MATE_SCORE,
    MATE_THRESHOLD,
    TABLEBASE,
    GameAnalysis,
)
from chess_analyzer.insights import CATEGORIES, CATEGORY_LABELS
from chess_analyzer.openings import OpeningDeviation
from chess_analyzer.parse import opening_name
from chess_analyzer.puzzles import solution_line


def format_eval(cp: int | None, source: str | None = None) -> str:
    """Format a White-POV evaluation, e.g. ``+1.25``, ``-M3`` or ``#`` (mated).

    Tablebase results are shown as ``TB 1-0``, ``TB draw`` or ``TB 0-1``, and
    unevaluated book positions as ``book``.
    """
    if cp is None:
        return "book"
    if source == TABLEBASE:
        return "TB 1-0" if cp > 0 else "TB 0-1" if cp < 0 else "TB draw"
    if abs(cp) >= MATE_THRESHOLD:
        moves = MATE_SCORE - abs(cp)
        sign = "+" if cp > 0 else "-"
        return f"{sign}M{moves}" if moves else f"{sign}#"
    return f"{cp / 100:+.2f}"


def _color_name(color: chess.Color) -> str:
    return "White" if color == chess.WHITE else "Black"


def format_game_report(
    game: chess.pgn.Game,
    color: chess.Color | None,
    analysis: GameAnalysis | None = None,
    deviation: OpeningDeviation | None = None,
    max_alternatives: int = 3,
    show_opening: bool = True,
) -> str:
    """Build a report for one game.

    Parameters
    ----------
    game : chess.pgn.Game
        The game.
    color : chess.Color or None
        The colour to report on. ``None`` reports on both sides.
    analysis : GameAnalysis, optional
        Engine analysis. The engine section is left out when missing.
    deviation : OpeningDeviation, optional
        Where the game left theory.
    max_alternatives : int, optional
        Number of book moves to suggest at the deviation.
    show_opening : bool, optional
        Whether to include the opening section; ``False`` when no opening
        source was consulted.
    """
    h = game.headers
    lines = [
        (
            f"{h.get('White', '?')} ({h.get('WhiteElo', '?')}) vs "
            f"{h.get('Black', '?')} ({h.get('BlackElo', '?')})  {h.get('Result', '*')}"
        ),
    ]
    meta = [h.get("Date", ""), h.get("TimeControl", ""), opening_name(game) or ""]
    if meta := "  ".join(m for m in meta if m and "?" not in m):
        lines.append(meta)
    if link := h.get("Link") or h.get("Site"):
        lines.append(link)

    if show_opening and deviation is None:
        lines += ["", "Opening", "  Stayed in book for the whole opening window."]
    elif show_opening:
        lines += ["", "Opening"]
        who = _color_name(deviation.color)
        if color is not None:
            who = "You" if deviation.color == color else "Your opponent"
        lines.append(f"  {who} left theory with {deviation.label}.")
        if deviation.alternatives:
            lines.append("  Book moves in that position:")
            for move in deviation.alternatives[:max_alternatives]:
                score = move.score_for(deviation.color)
                detail = (
                    f"{score:.0%} score over {move.games} games"
                    if score is not None
                    else f"weight {move.games}"
                )
                lines.append(f"    {move.san:<8} {detail}")
        else:
            lines.append("  The position was not in the book at all.")

    if analysis is not None:
        colors = [color] if color is not None else [chess.WHITE, chess.BLACK]
        for c in colors:
            counts = analysis.counts(c)
            lines.append("")
            lines.append(
                f"{_color_name(c)}: average centipawn loss "
                f"{analysis.average_cp_loss(c):.0f}"
            )
            lines.append(
                "  " + ", ".join(f"{counts.get(k, 0)} {k}" for k in CLASSIFICATIONS)
            )
            worst = analysis.worst_moves(c)
            if worst:
                lines.append("  Biggest mistakes:")
            for m in worst:
                kind = f" ({CATEGORY_LABELS[m.category]})" if m.category else ""
                lines.append(
                    f"    {m.label:<12} {m.classification:<8} "
                    f"{format_eval(m.eval_before, m.source_before)} -> "
                    f"{format_eval(m.eval_after, m.source)}  "
                    f"best was {m.best_san}{kind}"
                )
            critical = analysis.critical_moments(c)
            if critical:
                lines.append("  Critical moments (only one move kept the balance):")
            for m in critical:
                verdict = (
                    "found"
                    if m.classification in FOUND
                    else f"missed, best was {m.best_san}"
                )
                lines.append(
                    f"    {m.label:<12} {verdict}; {m.second_san} would give "
                    f"{format_eval(m.second_eval)}"
                )
            if time := format_time_summary(analysis.time_summary(c)):
                lines.append(f"  Time: {time}")
    if analysis is not None:
        sources = analysis.sources
        total = sum(sources.values())
        detail = ", ".join(
            f"{sources[k]} {k}"
            for k in ("book", "tablebase", "forced", "terminal")
            if sources[k]
        )
        lines.append("")
        lines.append(
            f"Engine searched {sources['engine']} of {total} positions"
            + (f" ({detail})." if detail else ".")
        )
    return "\n".join(lines)


def format_time_summary(time: dict | None) -> str:
    """Describe clock use, e.g. ``8s per move; 1 mistake in time trouble``."""
    if not time:
        return ""
    parts = [f"{time['average']:.0f}s per move"]
    if n := time["time_trouble"]:
        parts.append(f"{_count(n, 'mistake')} in time trouble")
    if n := time["impulsive"]:
        parts.append(f"{_count(n, 'mistake')} played in under 3s")
    if n := time["wasted_time"]:
        parts.append(f"{_count(n, 'long think')} on an obvious move")
    return "; ".join(parts)


def _count(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def format_player_stats(stats: dict, username: str) -> str:
    """Build the report on trends and weaknesses across several games.

    ``stats`` comes from :func:`chess_analyzer.stats.player_stats`.
    """
    games = stats["games"]
    lines = [f"Summary of {len(games)} games by {username}"]
    if stats["acpl"] is not None:
        lines.append(f"  Average centipawn loss: {stats['acpl']}")
    if games:
        lines += ["", "Centipawn loss per game (oldest first)"]
        for g in games:
            result = g["result"] or ""
            lines.append(
                f"  {g['date'] or '?':<10}  {g['acpl']:>4}  {result:<4}  "
                f"vs {g['opponent'] or '?'}"
            )
    if trend := stats["trend"]:
        lines.append(
            f"  Trend: {trend['direction']} ({trend['older']} in earlier games, "
            f"{trend['recent']} in the last {trend['games']})"
        )
    if stats["phases"]:
        lines += ["", "Centipawn loss by phase"]
        for phase, data in stats["phases"].items():
            lines.append(f"  {phase:<11} {data['acpl']:>4}  ({data['moves']} moves)")
    if stats["categories"]:
        lines += ["", "Kinds of mistakes and blunders"]
        for category in CATEGORIES:
            if count := stats["categories"].get(category):
                lines.append(f"  {CATEGORY_LABELS[category]:<22} {count}")
    if stats["openings"]:
        lines += [
            "",
            "Openings (move where you left book, on average, and pawn breaks)",
        ]
        for o in stats["openings"]:
            left = f"move {o['left_book']:g}" if o["left_book"] is not None else "-"
            lines.append(
                f"  {o['name'][:34]:<34} {o['color']:<5}  {o['games']} games  "
                f"left book: {left}"
            )
            for plan in o.get("plans", []):
                lines.append(f"    {format_plan(plan)}")
    if stats["critical"]["total"]:
        critical = stats["critical"]
        found = f"{critical['found']} of {critical['total']}"
        lines += ["", f"Critical moments: you found the only good move in {found}"]
    if stats["puzzles"]:
        lines += ["", "Puzzles from moves you missed (the position before the move)"]
        for puzzle in stats["puzzles"]:
            lines.append(
                f"  {puzzle['date'] or '?':<10}  {puzzle['label']:<12} "
                f"{puzzle['kind']:<9}  {puzzle['fen']}"
            )
    if time := format_time_summary(stats["time"]):
        lines += ["", f"Time: {time}"]
    if stats["insights"]:
        lines += ["", "What to work on"]
        lines += [f"  - {line}" for line in stats["insights"]]
    return "\n".join(lines)


def format_plan(plan: dict) -> str:
    """Describe how often a pawn break was carried out, from :mod:`chess_analyzer.stats`."""
    parts = [f"played in {plan['played'] + plan['unsound']} of {plan['games']}"]
    if plan["unsound"]:
        parts.append(f"{plan['unsound']} unsound")
    if plan["missed"]:
        parts.append(f"missed in {plan['missed']}")
    for key, word in (("with", "with it"), ("without", "without")):
        if plan[f"games_{key}"] and plan[f"score_{key}"] is not None:
            parts.append(f"{plan[f'score_{key}']:.0%} score {word}")
    return f"{plan['name']:<6} break: {', '.join(parts)}"


def format_puzzles_pgn(puzzles: list[dict]) -> str:
    """Write the moves a player missed as PGN puzzles.

    Each puzzle starts from the position before the missed move, with the
    solution as the main line: the move that should have been played and,
    when the engine's line is known, the forcing moves after it (see
    :func:`chess_analyzer.puzzles.solution_line`). ``puzzles`` come from
    :func:`chess_analyzer.stats.puzzles`. Lichess studies and most chess apps
    can import the result.
    """
    games = []
    for puzzle in puzzles:
        game = chess.pgn.Game()
        board = chess.Board(puzzle["fen"])
        game.setup(board)
        # The game move would give the solution away: name the move number.
        side = "White" if board.turn == chess.WHITE else "Black"
        move_number = (puzzle["ply"] + 1) // 2
        game.headers["Event"] = f"Puzzle, move {move_number}, {side} to move"
        game.headers["Site"] = puzzle.get("link") or "?"
        game.headers["Date"] = (puzzle.get("date") or "????-??-??").replace("-", ".")
        game.headers["Result"] = "*"
        if opponent := puzzle.get("opponent"):
            game.headers["Opponent"] = opponent
        line = solution_line(board, puzzle.get("line") or [puzzle["best_uci"]])
        node = game
        for move in line:
            node = node.add_variation(move)
        if line:
            what = (
                "the only move that kept the balance was missed"
                if puzzle.get("kind") == "critical"
                else f"a {puzzle.get('kind', 'mistake')}"
            )
            game.next().comment = f"In the game, {puzzle['label']} was played: {what}."
        games.append(str(game))
    return "\n\n".join(games) + "\n" if games else ""
