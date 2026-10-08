"""Check whether a player carried out the typical pawn breaks of their opening.

Knowing the book moves helps only until the opponent deviates. What carries
over into the middlegame is the opening's structure and the pawn breaks that
free it: in the French Defense, Black attacks White's pawn chain with ...c5
and ...f6. For each game, every typical break of the player's opening is
checked against the moves: was it played (and did it cost anything), did the
engine want it while the player never played it, or did it not come up?

This works on the move dictionaries of :func:`chess_analyzer.serialize.result_to_dict`,
so stored analyses need no new engine time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import chess

from chess_analyzer.engine import ERRORS

# Breaks are looked for in the first MAX_MOVE moves; later pawn moves belong
# to the endgame rather than the opening's plan.
MAX_MOVE = 25

PLAYED, UNSOUND, MISSED, NOT_PLAYED = "played", "unsound", "missed", "not_played"
STATUSES = (PLAYED, UNSOUND, MISSED, NOT_PLAYED)


@dataclass(frozen=True)
class Break:
    """A pawn break: a pawn of the player's colour pushed up its file to ``square``."""

    square: str
    goal: str

    def label(self, color: chess.Color) -> str:
        return self.square if color == chess.WHITE else f"...{self.square}"


W, B = chess.WHITE, chess.BLACK

# Openings, matched by keywords in their name (first match wins, so the more
# specific names come first), with the typical breaks for each side.
OPENING_PLANS: tuple[
    tuple[tuple[str, ...], dict[chess.Color, tuple[Break, ...]]], ...
] = (
    (
        ("french",),
        {
            B: (
                Break("c5", "hit the base of White's d4-e5 pawn chain"),
                Break("f6", "undermine the e5 pawn and open the f-file"),
            ),
            W: (Break("f5", "attack Black's e6 pawn and the kingside"),),
        },
    ),
    (
        ("caro kann",),
        {
            B: (
                Break("c5", "challenge White's d4 pawn"),
                Break("e5", "free the position in the centre"),
            ),
            W: (Break("c4", "open the c-file against the d5 pawn"),),
        },
    ),
    (
        ("sicilian",),
        {
            B: (
                Break("d5", "free the centre, Black's main break in the Sicilian"),
                Break("b5", "gain space and attack on the queenside"),
            ),
            W: (
                Break("f5", "attack on the kingside and the e6/d5 squares"),
                Break("e5", "drive away Black's knight from f6"),
            ),
        },
    ),
    (
        ("scandinavian",),
        {
            B: (
                Break("c5", "challenge White's d4 pawn"),
                Break("e5", "fight for the centre"),
            ),
        },
    ),
    (
        ("pirc",),
        {
            B: (Break("e5", "strike at the centre"), Break("c5", "hit d4")),
            W: (Break("e5", "gain space and push away the f6 knight"),),
        },
    ),
    (
        ("modern defense",),
        {
            B: (Break("e5", "strike at the centre"), Break("c5", "hit d4")),
            W: (Break("e5", "gain space in the centre"),),
        },
    ),
    (
        ("ruy lopez",),
        {
            W: (Break("d4", "take the centre once it is prepared"),),
            B: (
                Break("d5", "strike back in the centre"),
                Break("c5", "gain space on the queenside"),
            ),
        },
    ),
    (
        ("italian", "giuoco piano", "two knights"),
        {
            W: (Break("d4", "take the centre once it is prepared"),),
            B: (Break("d5", "free the position in the centre"),),
        },
    ),
    (
        ("queens gambit declined", "semi slav", "tarrasch defense", "ragozin"),
        {
            B: (
                Break("c5", "challenge White's centre"),
                Break("e5", "free Black's position"),
            ),
            W: (
                Break("e4", "take the centre"),
                Break("b5", "minority attack against c6"),
            ),
        },
    ),
    (
        ("slav",),
        {
            B: (
                Break("e5", "free Black's position"),
                Break("c5", "challenge White's centre"),
            ),
            W: (Break("e4", "take the centre"),),
        },
    ),
    (
        ("queens gambit accepted",),
        {
            B: (
                Break("c5", "hit White's d4 pawn"),
                Break("e5", "fight for the centre"),
            ),
            W: (Break("d5", "gain space in the centre"),),
        },
    ),
    (
        ("kings indian defense",),
        {
            B: (
                Break("e5", "fight for the centre"),
                Break("f5", "attack on the kingside"),
            ),
            W: (Break("c5", "attack on the queenside"),),
        },
    ),
    (
        ("nimzo indian", "queens indian", "bogo indian"),
        {
            B: (
                Break("c5", "hit White's d4 pawn"),
                Break("e5", "fight for the centre"),
            ),
            W: (Break("e4", "take the centre"),),
        },
    ),
    (
        ("grunfeld", "gruenfeld"),
        {
            B: (Break("c5", "pressure White's d4 pawn"),),
            W: (Break("d5", "gain space in the centre"),),
        },
    ),
    (
        ("benoni", "benko"),
        {
            B: (
                Break("b5", "expand on the queenside"),
                Break("f5", "challenge White's e4 pawn"),
            ),
            W: (Break("e5", "break through in the centre"),),
        },
    ),
    (
        ("dutch",),
        {
            B: (Break("e5", "free Black's position in the centre"),),
            W: (Break("e4", "open the centre against the f5 pawn"),),
        },
    ),
    (
        ("london",),
        {
            W: (Break("e4", "take the centre"), Break("c4", "challenge d5")),
            B: (
                Break("c5", "hit White's d4 pawn"),
                Break("e5", "fight for the centre"),
            ),
        },
    ),
    (
        ("english",),
        {
            W: (
                Break("d4", "open the centre"),
                Break("b4", "expand on the queenside"),
            ),
            B: (Break("d5", "fight for the centre"),),
        },
    ),
)


def _normalise(name: str) -> str:
    name = name.lower().replace("defence", "defense").replace("ü", "u")
    return re.sub(r"[^a-z0-9]+", " ", name.replace("'", "")).strip()


def opening_breaks(name: str | None, color: chess.Color) -> tuple[Break, ...]:
    """Return the typical pawn breaks for ``color`` in the opening ``name``."""
    if not name:
        return ()
    name = f" {_normalise(name)} "
    for keywords, plans in OPENING_PLANS:
        if any(f" {k} " in name for k in keywords):
            return plans.get(color, ())
    return ()


def _is_break(fen: str, uci: str | None, square: chess.Square) -> bool:
    """Whether ``uci`` pushes a pawn up its own file to ``square``."""
    if not uci:
        return False
    move = chess.Move.from_uci(uci)
    if move.to_square != square:
        return False
    if chess.square_file(move.from_square) != chess.square_file(square):
        return False
    piece = chess.Board(fen).piece_at(move.from_square)
    return piece is not None and piece.piece_type == chess.PAWN


def check_plans(name: str | None, color: str, moves: list[dict]) -> list[dict]:
    """Check each typical break of the opening for the player of ``color``.

    Parameters
    ----------
    name : str or None
        The opening's name.
    color : {"white", "black"}
        The player's colour.
    moves : list of dict
        The game's moves, as in :func:`chess_analyzer.serialize.move_to_dict`.

    Returns
    -------
    list of dict
        One per break, with its ``name`` (e.g. ``...c5``), ``goal`` and
        ``status``: ``played``, ``unsound`` (played, but a mistake or
        blunder), ``missed`` (the engine wanted it but it was never played)
        or ``not_played``. ``ply`` and ``label`` give the move that played it,
        or the first position where the engine wanted it; ``late`` is true
        when the break was played after the engine had first wanted it.
    """
    side = chess.WHITE if color == "white" else chess.BLACK
    own = [
        m
        for m in moves
        if m.get("color") == color and m.get("fen_before") and m["ply"] <= 2 * MAX_MOVE
    ]
    checks = []
    for brk in opening_breaks(name, side):
        square = chess.parse_square(brk.square)
        played = next(
            (m for m in own if _is_break(m["fen_before"], m["uci"], square)), None
        )
        wanted = next(
            (
                m
                for m in own
                if m is not played
                and _is_break(m["fen_before"], m.get("best_uci"), square)
            ),
            None,
        )
        check = {"name": brk.label(side), "goal": brk.goal}
        if played is not None:
            unsound = played.get("classification") in ERRORS
            check.update(
                status=UNSOUND if unsound else PLAYED,
                ply=played["ply"],
                label=played["label"],
                late=wanted is not None and wanted["ply"] < played["ply"],
            )
        elif wanted is not None:
            check.update(
                status=MISSED, ply=wanted["ply"], label=wanted["label"], late=False
            )
        else:
            check.update(status=NOT_PLAYED, ply=None, label=None, late=False)
        checks.append(check)
    return checks
