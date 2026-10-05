"""Turn analysis results into a plain-text report."""

from __future__ import annotations

import chess
import chess.pgn

from chess_analyzer.engine import (
    CLASSIFICATIONS,
    MATE_SCORE,
    TABLEBASE,
    GameAnalysis,
)
from chess_analyzer.openings import OpeningDeviation
from chess_analyzer.parse import opening_name


def format_eval(cp: int | None, source: str | None = None) -> str:
    """Format a White-POV evaluation, e.g. ``+1.25``, ``-M3`` or ``#`` (mated).

    Tablebase results are shown as ``TB 1-0``, ``TB draw`` or ``TB 0-1``, and
    unevaluated book positions as ``book``.
    """
    if cp is None:
        return "book"
    if source == TABLEBASE:
        return "TB 1-0" if cp > 0 else "TB 0-1" if cp < 0 else "TB draw"
    if abs(cp) >= MATE_SCORE - 500:
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
                lines.append(
                    f"    {m.label:<12} {m.classification:<8} "
                    f"{format_eval(m.eval_before, m.source_before)} -> "
                    f"{format_eval(m.eval_after, m.source)}  "
                    f"best was {m.best_san}"
                )
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
