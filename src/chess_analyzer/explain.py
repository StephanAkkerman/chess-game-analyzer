"""Explain moves in plain English instead of with engine lines.

The engine says *that* a move is good or bad; this module says *why*. For a
move of an analysed game it works out concrete facts with python-chess:

- what the move does: captures, checks, threats and forks, pins, piece
  development, the centre, castling, passed pawns, pieces it defends or
  leaves hanging;
- for a mistake, what the opponent's best reply does to punish it;
- what the engine's best move would have done instead, and where its line
  ends up in material.

The facts are joined into a short explanation with fixed sentence patterns,
so it never says anything the board doesn't show and needs no model. A
language model can reword them into a more natural paragraph (see
:mod:`chess_analyzer.coach`), but the facts stay the source of truth.

Everything works on the move dictionaries of a stored analysis (see
:func:`chess_analyzer.serialize.move_to_dict`), so analyses made before this
module existed can be explained too.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import chess

from chess_analyzer.insights import PIECE_VALUES

# Mirrors chess_analyzer.engine, which this module does not import so it stays
# light: scores at least MATE_THRESHOLD are forced mates.
MATE_THRESHOLD = 9_500
CENTRE = chess.SquareSet([chess.D4, chess.E4, chess.D5, chess.E5])
# A line has to win at least this much (in pawns) to say it wins material.
MATERIAL_GAIN = 2
ERRORS = ("inaccuracy", "mistake", "miss", "blunder")
# What each side is, from the side to move's point of view, by evaluation.
VERDICTS = (
    (MATE_THRESHOLD, "a forced mate"),
    (300, "a winning position"),
    (150, "a clearly better position"),
    (50, "a slightly better position"),
    (-49, "a roughly equal position"),
    (-149, "a slightly worse position"),
    (-299, "a clearly worse position"),
    (-MATE_THRESHOLD + 1, "a losing position"),
)

MOVE, REPLY, BEST = "move", "reply", "best"
# What an opponent's reply can do that punishes a move.
PUNISHING = (
    "mate",
    "wins",
    "capture",
    "recapture",
    "trade",
    "promotes",
    "check",
    "fork",
    "threat",
    "pressure",
    "pin",
    "passed",
)


@dataclass
class Fact:
    """One thing a move does, as a sentence fragment.

    ``about`` says which move it describes: the game move (``move``), the
    opponent's best reply to it (``reply``) or the engine's best move
    (``best``). ``kind`` is a short tag such as ``fork`` or ``hangs``.
    """

    about: str
    kind: str
    text: str


def _side(color: chess.Color) -> str:
    return "White" if color == chess.WHITE else "Black"


def _piece(board: chess.Board, square: chess.Square) -> str:
    piece = board.piece_at(square)
    name = chess.piece_name(piece.piece_type) if piece else "piece"
    return f"{name} on {chess.square_name(square)}"


def _and(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def is_hanging(board: chess.Board, square: chess.Square) -> bool:
    """Return whether the piece on ``square`` can be won.

    That is the case when the opponent attacks it and it is undefended, or
    when it is attacked by a cheaper piece. Pawns and kings never hang.
    """
    piece = board.piece_at(square)
    if piece is None or piece.piece_type in (chess.PAWN, chess.KING):
        return False
    attackers = board.attackers(not piece.color, square)
    if not attackers:
        return False
    if not board.attackers(piece.color, square):
        return True
    value = PIECE_VALUES[piece.piece_type]
    return any(
        PIECE_VALUES[board.piece_type_at(a)] < value
        for a in attackers
        if board.piece_type_at(a) is not None
    )


def is_passed(board: chess.Board, square: chess.Square) -> bool:
    """Return whether the pawn on ``square`` has no enemy pawns in its way."""
    piece = board.piece_at(square)
    if piece is None or piece.piece_type != chess.PAWN:
        return False
    file, rank = chess.square_file(square), chess.square_rank(square)
    for enemy in board.pieces(chess.PAWN, not piece.color):
        if abs(chess.square_file(enemy) - file) > 1:
            continue
        ahead = chess.square_rank(enemy) - rank
        if (ahead > 0) if piece.color == chess.WHITE else (ahead < 0):
            return False
    return True


def _targets(board: chess.Board, square: chess.Square) -> list[chess.Square]:
    """Enemy pieces the piece on ``square`` attacks and could win.

    A target is the king (a check), a piece worth more than the attacker, or
    an undefended piece other than a pawn.
    """
    piece = board.piece_at(square)
    if piece is None:
        return []
    value = PIECE_VALUES[piece.piece_type] or 100
    found = []
    for target in board.attacks(square):
        victim = board.piece_at(target)
        if victim is None or victim.color == piece.color:
            continue
        if (
            victim.piece_type == chess.KING
            or victim.piece_type != chess.PAWN
            and (
                PIECE_VALUES[victim.piece_type] > value
                or not board.attackers(victim.color, target)
            )
        ):
            found.append(target)
    return found


def material(board: chess.Board, color: chess.Color) -> int:
    """Material of ``color`` minus that of the opponent, in pawns."""
    total = 0
    for piece_type, value in PIECE_VALUES.items():
        total += value * len(board.pieces(piece_type, color))
        total -= value * len(board.pieces(piece_type, not color))
    return total


def move_facts(
    board: chess.Board,
    move: chess.Move,
    about: str = MOVE,
    recapture_on: chess.Square | None = None,
) -> list[Fact]:
    """Describe what ``move`` does in ``board``, most important first.

    ``recapture_on`` is the square on which the move that led to ``board``
    captured, if it did: taking back there is a recapture.
    """
    facts: list[Fact] = []

    def add(kind: str, text: str) -> None:
        facts.append(Fact(about, kind, text))

    mover = board.turn
    piece = board.piece_at(move.from_square)
    if piece is None or move not in board.legal_moves:
        return facts
    name = chess.piece_name(piece.piece_type)
    after = board.copy(stack=False)
    after.push(move)

    if after.is_checkmate():
        add("mate", "delivers checkmate")
        return facts

    if board.is_castling(move):
        side = "kingside" if chess.square_file(move.to_square) > 4 else "queenside"
        add("castles", f"castles {side}, bringing the king to safety")
    elif board.is_capture(move):
        if board.is_en_passant(move):
            add("capture", "takes the pawn en passant")
        else:
            victim = board.piece_at(move.to_square)
            target = _piece(board, move.to_square)
            defended = bool(after.attackers(not mover, move.to_square))
            if recapture_on == move.to_square:
                add("recapture", f"takes back on {chess.square_name(move.to_square)}")
            elif not defended:
                add("wins", f"wins the {target} for free")
            elif PIECE_VALUES[victim.piece_type] > PIECE_VALUES[piece.piece_type]:
                add("wins", f"wins the {target} with a cheaper piece")
            elif victim.piece_type == chess.QUEEN and piece.piece_type == chess.QUEEN:
                add("trade", "trades queens")
            else:
                add("capture", f"takes the {target}")
    if move.promotion:
        add("promotes", f"promotes to a {chess.piece_name(move.promotion)}")

    targets = _targets(after, move.to_square)
    others = [t for t in targets if after.piece_type_at(t) != chess.KING]
    if after.is_check():
        if others:
            add("fork", f"gives check and also attacks the {_piece(after, others[0])}")
        else:
            add("check", "gives check")
    elif len(others) >= 2:
        names = [_piece(after, t) for t in others[:2]]
        add("fork", f"forks the {names[0]} and the {names[1]}")
    elif others:
        add("threat", f"attacks the {_piece(after, others[0])}")
    else:
        # A piece or pawn the moved piece now attacks once more than it is
        # defended, such as f7 in many openings.
        for target in after.attacks(move.to_square):
            victim = after.piece_at(target)
            if (
                victim is None
                or victim.color == mover
                or victim.piece_type == chess.KING
            ):
                continue
            attackers = len(after.attackers(mover, target))
            defenders = len(after.attackers(not mover, target))
            if attackers > defenders and len(board.attackers(mover, target)) <= len(
                board.attackers(not mover, target)
            ):
                add("pressure", f"puts pressure on the {_piece(after, target)}")
                break

    # Pins against the king that this move creates.
    for square in chess.SquareSet(after.occupied_co[not mover]):
        if (
            after.piece_type_at(square) != chess.KING
            and after.is_pinned(not mover, square)
            and not board.is_pinned(not mover, square)
        ):
            add("pin", f"pins the {_piece(after, square)} to the king")
            break

    if piece.piece_type in (chess.KNIGHT, chess.BISHOP):
        home = 0 if mover == chess.WHITE else 7
        if chess.square_rank(move.from_square) == home and board.fullmove_number <= 15:
            add("develops", f"develops the {name}")
    if piece.piece_type == chess.PAWN and move.to_square in CENTRE:
        add("centre", "claims space in the centre")
    elif piece.piece_type != chess.PAWN and not board.is_castling(move):
        before = len(board.attacks(move.from_square) & CENTRE)
        now = len(after.attacks(move.to_square) & CENTRE)
        if now >= 2 and now > before:
            add("centre", "controls the centre")

    if (
        piece.piece_type == chess.KING
        and not board.is_castling(move)
        and board.has_castling_rights(mover)
        and chess.popcount(board.queens | board.rooks) > 2
    ):
        add("king", "gives up the right to castle, leaving the king exposed")

    if piece.piece_type == chess.PAWN and is_passed(after, move.to_square):
        if not is_passed(board, move.from_square) or board.is_capture(move):
            add("passed", "creates a passed pawn")
        elif chess.square_rank(move.to_square) in (5, 2):
            add("passed", "pushes the passed pawn closer to promotion")

    # Friendly pieces that were hanging and are now safe.
    for square in chess.SquareSet(board.occupied_co[mover]):
        if square == move.from_square:
            continue
        if is_hanging(board, square) and not is_hanging(after, square):
            add("defends", f"defends the {_piece(board, square)}")
            break

    # Friendly pieces this move leaves hanging, the moved piece first.
    if not after.is_check():
        squares = [move.to_square] + [
            s for s in chess.SquareSet(after.occupied_co[mover]) if s != move.to_square
        ]
        traded = (
            board.is_capture(move)
            and not board.is_en_passant(move)
            and PIECE_VALUES[board.piece_type_at(move.to_square)]
            >= PIECE_VALUES[piece.piece_type]
        )
        for square in squares:
            if square == move.to_square and traded:
                continue
            was = square != move.to_square and is_hanging(board, square)
            if is_hanging(after, square) and not was:
                add("hangs", f"leaves the {_piece(after, square)} unprotected")
                break
    return facts


def _verdict(cp: int | None) -> str:
    if cp is None:
        return ""
    for threshold, words in VERDICTS:
        if cp >= threshold:
            return words
    return "a forced mate for the opponent"


def _for(cp: int | None, color: chess.Color) -> int | None:
    """``cp`` (from White's point of view) for ``color``."""
    if cp is None:
        return None
    return cp if color == chess.WHITE else -cp


def _uci(board: chess.Board, uci: str) -> chess.Move | None:
    try:
        move = chess.Move.from_uci(uci)
    except (ValueError, TypeError):
        return None
    return move if move in board.legal_moves else None


def line_outcome(
    board: chess.Board, line: list[str], color: chess.Color
) -> tuple[str, int, bool]:
    """Play the engine ``line`` from ``board``.

    Returns the line in SAN, how much material ``color`` gains along it (in
    pawns) and whether it ends in checkmate. Illegal moves end the line.

    A line can stop in the middle of an exchange, so the gain is the smaller
    of the material after its last two moves.
    """
    position = board.copy(stack=False)
    start = material(position, color)
    played = []
    balances = [start]
    for uci in line:
        move = _uci(position, uci)
        if move is None:
            break
        played.append(move)
        position.push(move)
        balances.append(material(position, color))
    san = board.variation_san(played) if played else ""
    return san, min(balances[-2:]) - start, position.is_checkmate()


def _sentence(subject: str, facts: list[Fact]) -> str:
    if not facts:
        return ""
    return f"{subject} {_and([f.text for f in facts[:3]])}."


def explain_move(
    move: dict,
    previous: dict | None = None,
    hide_best: bool = False,
) -> dict:
    """Explain one move of an analysis in plain English.

    Parameters
    ----------
    move : dict
        A move of an analysis, as made by
        :func:`chess_analyzer.serialize.move_to_dict`.
    previous : dict, optional
        The move before it, which tells recaptures from captures.
    hide_best : bool
        Leave out the engine's best move, for a user who wants to find it
        themselves first.

    Returns
    -------
    dict
        ``text``: the explanation; ``facts``: the facts it was built from,
        as dictionaries of :class:`Fact`.
    """
    board = chess.Board(move["fen_before"])
    color = board.turn
    side, opponent = _side(color), _side(not color)
    played = _uci(board, move.get("uci", ""))
    classification = move.get("classification", "")
    prior = None
    if previous:
        previous_board = chess.Board(previous["fen_before"])
        previous_move = _uci(previous_board, previous.get("uci", ""))
        if previous_move and previous_board.is_capture(previous_move):
            prior = previous_move.to_square
    facts: list[Fact] = []
    sentences: list[str] = []
    if played is None:
        return {"text": "", "facts": []}

    label = move.get("label", move.get("san", ""))
    before = _for(move.get("eval_before"), color)
    after = _for(move.get("eval_after"), color)
    if classification == "book":
        sentences.append(f"{label} is a known opening move.")
    elif classification == "forced":
        sentences.append(f"{label} was the only legal move.")
    elif before is not None and after is not None:
        if classification in ERRORS and _verdict(before) != _verdict(after):
            sentences.append(
                f"{label} is {_article(classification)}: it turns "
                f"{_verdict(before)} for {side} into {_verdict(after)}."
            )
        elif classification in ERRORS:
            sentences.append(
                f"{label} is {_article(classification)}, though {side} keeps "
                f"{_verdict(after)}."
            )
        elif classification == "brilliant":
            sentences.append(
                f"{label} is brilliant: it gives up material, yet {side} keeps "
                f"{_verdict(after)}."
            )
        elif classification == "great":
            sentences.append(
                f"{label} is the only move that holds, keeping {_verdict(after)} "
                f"for {side}."
            )
        elif classification == "best":
            sentences.append(
                f"{label} is the engine's choice and keeps {_verdict(after)} "
                f"for {side}."
            )
        else:
            sentences.append(f"{label} is fine and keeps {_verdict(after)} for {side}.")

    own = move_facts(board, played, MOVE, prior)
    facts += own
    if own:
        sentences.append(_sentence("It" if sentences else label, own))
    elif classification not in ("book", "forced"):
        sentences.append(f"{'It' if sentences else label} is a quiet move.")

    position = board.copy(stack=False)
    position.push(played)
    reply = (
        _uci(position, move.get("reply_uci", "")) if classification in ERRORS else None
    )
    if reply is not None:
        answer = [
            f
            for f in move_facts(
                position,
                reply,
                REPLY,
                played.to_square if board.is_capture(played) else None,
            )
            if f.kind in PUNISHING
        ]
        facts += answer
        reply_san = position.san(reply)
        if answer:
            sentences.append(
                f"The problem is that {opponent} answers {reply_san}, which "
                f"{_and([f.text for f in answer[:2]])}."
            )
        else:
            sentences.append(f"{opponent}'s best answer is {reply_san}.")

    best = _uci(board, move.get("best_uci", ""))
    if best is not None and best != played and not hide_best:
        better = [f for f in move_facts(board, best, BEST, prior) if f.kind != "hangs"]
        facts += better
        best_san = board.san(best)
        start = "Better was" if classification in ERRORS else "The engine preferred"
        text = f"{start} {best_san}"
        if better:
            text += f", which {_and([f.text for f in better[:2]])}"
        line = move.get("best_line") or []
        if len(line) > 1:
            san, gained, mated = line_outcome(board, line, color)
            if mated:
                text += f"; after {san} it is mate"
                facts.append(Fact(BEST, "line_mate", f"the line {san} ends in mate"))
            elif gained >= MATERIAL_GAIN:
                text += f"; after {san} {side} is {_pawns(gained)} up in material"
                facts.append(
                    Fact(BEST, "line_material", f"the line {san} wins {_pawns(gained)}")
                )
        sentences.append(text + ".")
    elif best is not None and best != played and hide_best:
        sentences.append("There was a stronger move: try to find it.")
    return {
        "text": " ".join(s for s in sentences if s),
        "facts": [asdict(f) for f in facts],
    }


def _article(word: str) -> str:
    return f"an {word}" if word[0] in "aeiou" else f"a {word}"


def _pawns(n: int) -> str:
    return "a pawn" if n == 1 else f"{n} pawns"


def explain_ply(result: dict, ply: int, hide_best: bool = False) -> dict | None:
    """Explain move ``ply`` (1-based) of a finished analysis ``result``."""
    moves = result.get("moves") or []
    if not 1 <= ply <= len(moves):
        return None
    previous = moves[ply - 2] if ply > 1 else None
    return explain_move(moves[ply - 1], previous, hide_best=hide_best)
