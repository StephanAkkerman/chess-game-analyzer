"""Sum a game up in one lesson.

After a game a player wants to know one thing: what to learn from it. This
module picks it from the analysis of one side:

- the turning point, the move that cost that side the most of its winning
  chances, with what was wrong with it and the move that would have kept the
  game (see :func:`chess_analyzer.explain.explain_move`), and how to avoid
  the tactic that punished it or else a tip for that kind of mistake;
- or, when that side made no mistake, what went well, so the player knows
  what to keep doing.

As with the move explanations, the takeaway is put together from the stored
analysis with fixed sentences, so it needs no model.
"""

from __future__ import annotations

import math

from chess_analyzer.explain import MATE_THRESHOLD, explain_move
from chess_analyzer.weaknesses import CATEGORY_ADVICE

# Moves that can turn a game; inaccuracies rarely do on their own.
TURNING = ("mistake", "miss", "blunder")
FOUND = ("brilliant", "great", "best")
# Evaluations beyond this are as good as won, as in Lichess's accuracy.
EVAL_CAP = 1_000
# A side this far ahead (in centipawns) that only draws let a win slip.
WINNING = 150
WON, LOST, DREW = "won", "lost", "drew"
TITLES = {
    LOST: "What could have saved the game",
    DREW: "Where the game slipped away",
    WON: "What to clean up, even in a win",
    None: "The turning point",
}
TIME_TIPS = {
    "impulsive": "It was played in under 3 seconds: a moment more to check "
    "your opponent's replies would have helped.",
    "time_trouble": "It was played in time trouble: leave yourself more clock "
    "for moments like this.",
}


def win_chance(cp: int) -> float:
    """Chance to win, from 0 to 100, of a side with evaluation ``cp``.

    This is the curve Lichess fitted to its games.
    """
    if abs(cp) >= MATE_THRESHOLD:
        cp = EVAL_CAP if cp > 0 else -EVAL_CAP
    cp = max(-EVAL_CAP, min(EVAL_CAP, cp))
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


def _for(cp: int, color: str) -> int:
    return cp if color == "white" else -cp


def outcome(result: dict, color: str) -> str | None:
    """Whether ``color`` won, lost or drew the game, if the PGN says."""
    score = (result.get("headers") or {}).get("Result")
    if score == "1/2-1/2":
        return DREW
    if score in ("1-0", "0-1"):
        return WON if (score == "1-0") == (color == "white") else LOST
    return None


def swing(move: dict) -> float:
    """How many winning chances ``move`` cost the side that played it."""
    if move.get("eval_before") is None or move.get("eval_after") is None:
        return 0.0
    color = move["color"]
    return win_chance(_for(move["eval_before"], color)) - win_chance(
        _for(move["eval_after"], color)
    )


def turning_point(moves: list[dict], color: str) -> dict | None:
    """The mistake of ``color`` that cost the most winning chances."""
    errors = [
        m for m in moves if m["color"] == color and m["classification"] in TURNING
    ]
    # max keeps the earliest of equal swings.
    return max(errors, key=swing, default=None)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _and(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _strengths(result: dict, moves: list[dict], color: str) -> list[str]:
    """What ``color`` did well, most telling first."""
    own = [m for m in moves if m["color"] == color]
    found = []
    brilliant = [m for m in own if m["classification"] == "brilliant"]
    if brilliant:
        found.append(f"the sacrifice {brilliant[0]['label']}")
    held = [m for m in own if m.get("critical") and m["classification"] in FOUND]
    if held:
        labels = _and([m["label"] for m in held[:2]])
        found.append(f"finding the only good move with {labels}")
    played = [m for m in own if m["classification"] not in ("book", "forced")]
    top = [m for m in played if m["classification"] in FOUND]
    if played and len(top) * 2 >= len(played):
        found.append(
            f"playing the engine's top choice in {len(top)} of your {len(played)} moves"
        )
    summary = (result.get("summary") or {}).get(color) or {}
    if not found and summary.get("acpl") is not None and played:
        found.append(
            f"keeping your average loss to {summary['acpl']} centipawns a move"
        )
    if not (summary.get("counts") or {}).get("inaccuracy"):
        found.append("not giving away even small advantages")
    return found


def _clean(result: dict, moves: list[dict], color: str, verdict: str | None) -> dict:
    """The takeaway for a side without mistakes."""
    strengths = _strengths(result, moves, color)
    keep = f"Keep doing this: {_and(strengths[:3])}." if strengths else ""
    termination = (result.get("headers") or {}).get("Termination", "").lower()
    if verdict == LOST and "time" in termination:
        title = "Lost on the clock, not on the board"
        text = (
            "You made no mistake or blunder, but ran out of time. Your moves "
            "were sound, so the clock is what to work on: spend less time on "
            "the obvious moves."
        )
    elif verdict == LOST:
        title = "No single move lost it"
        text = (
            "You made no mistake or blunder: the game slipped away a little "
            "at a time. Look at your inaccuracies to see where small "
            "advantages went."
        )
    elif verdict == WON:
        title = "A clean win"
        text = "You won without a mistake or blunder."
    else:
        title = "A clean game"
        text = "You made no mistake or blunder."
    return {
        "kind": "clean",
        "title": title,
        "text": text,
        "advice": keep,
        "ply": None,
        "facts": [],
    }


def game_takeaway(result: dict, color: str, hide_best: bool = False) -> dict:
    """The one lesson of a finished analysis ``result`` for ``color``.

    Parameters
    ----------
    result : dict
        An analysis, as made by
        :func:`chess_analyzer.serialize.result_to_dict`.
    color : str
        ``white`` or ``black``: whose takeaway it is.
    hide_best : bool
        Leave out the move that would have been better, for a user who wants
        to find it themselves first.

    Returns
    -------
    dict
        ``kind``: ``turning_point`` or ``clean``; ``title``: a heading;
        ``text``: what happened; ``advice``: what to do next time, or keep
        doing; ``ply``: the turning point's move, if any; ``outcome``: whether
        ``color`` won, lost or drew, if known; ``facts``: as in
        :func:`chess_analyzer.explain.explain_move`.
    """
    moves = result.get("moves") or []
    verdict = outcome(result, color)
    move = turning_point(moves, color)
    if move is None:
        return {**_clean(result, moves, color, verdict), "outcome": verdict}

    ply = move["ply"]
    previous = moves[ply - 2] if ply > 1 else None
    explanation = explain_move(
        move, previous, hide_best=hide_best, history=moves[: ply - 1]
    )
    advice = []
    tip = CATEGORY_ADVICE.get(move.get("category") or "")
    if explanation["avoid"]:
        # How to avoid the tactic that punished it, more concrete than a
        # tip for the kind of mistake.
        advice.append(explanation["avoid"])
    elif tip:
        advice.append(f"Next time, {tip}.")
    if move.get("time_flag") in TIME_TIPS:
        advice.append(TIME_TIPS[move["time_flag"]])
    others = sum(
        1
        for m in moves
        if m["color"] == color and m["classification"] in TURNING and m is not move
    )
    if others:
        advice.append(
            f"You made {_plural(others, 'other costly move')}; the summary "
            "lists the worst."
        )
    title = TITLES[verdict]
    if verdict == DREW and _for(move["eval_before"], color) >= WINNING:
        title = "Where the win slipped away"
    return {
        "kind": "turning_point",
        "title": title,
        "text": explanation["text"],
        "advice": " ".join(advice),
        "ply": ply,
        "outcome": verdict,
        "facts": explanation["facts"],
    }
