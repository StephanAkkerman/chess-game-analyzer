"""Find the weaknesses that keep coming back across a player's games.

Overall numbers say *how much* a player loses; this module looks for
*where*: a kind of mistake that piles up in one phase of the game or one
opening ("You miss tactics in the endgame"), an opening or a colour that goes
worse than the rest, or an opening that eats the clock ("You spend a lot of
time in the opening of the Caro-Kann Defense").

As in :mod:`chess_analyzer.explain`, the facts are counted from the stored
analyses and put into fixed sentences, so no model is needed. A language
model can turn them into a coach's advice (see
:meth:`chess_analyzer.coach.Coach.reword_weaknesses`), but the numbers stay
the source of truth. Each weakness lists the moves behind it, newest first;
those the player got wrong can be practised as puzzles.
"""

from __future__ import annotations

from chess_analyzer.insights import (
    ALLOWED_MATE,
    ALLOWED_TACTIC,
    HUNG_PIECE,
    MISSED_MATE,
    MISSED_TACTIC,
    PHASES,
    REFUTED_ATTACK,
)

# A kind of mistake counts as piling up somewhere when it happened there at
# least MIN_ERRORS times, at least CONCENTRATION times as often per move as
# in the rest of the player's moves.
MIN_ERRORS = 3
CONCENTRATION = 2.0
# Openings and colours are compared once both sides of the comparison have
# this many games.
MIN_GAMES = 3
# An opening or colour goes worse when its average centipawn loss is at
# least WORSE_RATIO times, and WORSE_CP more than, that of the other games.
WORSE_RATIO = 1.5
WORSE_CP = 15
# An opening eats the clock when it uses at least CLOCK_GAP more of the
# starting time by move 15 (see stats.CLOCK_MOVES), and CLOCK_RATIO times as
# much, as the player's other openings.
CLOCK_GAP = 0.1
CLOCK_RATIO = 1.5
MAX_WEAKNESSES = 5
MAX_EXAMPLES = 5
# Ratios against nothing (no such mistakes elsewhere) sort as this.
MAX_RATIO = 5.0

CATEGORY_ADVICE = {
    "allowed_mate": "check your opponent's checks and threats against your "
    "king before every move",
    "missed_mate": "practise mating patterns",
    "hung_piece": "before each move, check which of your pieces it leaves undefended",
    "refuted_attack": "calculate attacks and sacrifices further before "
    "committing to them",
    "allowed_tactic": "before each move, look for your opponent's checks and "
    "captures in reply",
    "conversion": "practise converting winning endgames",
    "missed_tactic": "solve tactics puzzles to spot forcing moves",
    "positional": "study plans and pawn structures",
}
# What the player keeps doing, and what those mistakes are called, for the
# kinds that say something concrete. Conversion mistakes only happen in the
# endgame, and positional ones are what is left when nothing else applies.
CATEGORY_WORDS = {
    MISSED_TACTIC: ("miss tactics", "missed tactics"),
    HUNG_PIECE: ("hang pieces", "hung pieces"),
    ALLOWED_TACTIC: ("allow tactics", "tactics you allowed"),
    ALLOWED_MATE: ("allow mates", "mates you allowed"),
    MISSED_MATE: ("miss mates", "missed mates"),
    REFUTED_ATTACK: ("play unsound attacks", "unsound attacks"),
}


def _side(record: dict) -> str | None:
    """The opening family and the player's colour, e.g. ``French Defense as Black``."""
    if not record.get("family"):
        return None
    return f"{record['family']} as {record['color'].capitalize()}"


def _moves(record: dict) -> int:
    return sum(p["moves"] for p in record["phases"].values())


def _example(record: dict, error: dict, puzzles: set[int]) -> dict:
    return {
        "id": record["id"],
        "date": record["date"],
        "opponent": record["opponent"],
        "color": record["color"],
        **{k: error[k] for k in ("ply", "label", "classification", "best_san")},
        "puzzle": error["ply"] in puzzles,
    }


def _examples(records: list[dict], keep) -> list[dict]:
    """The player's errors for which ``keep(record, error)`` holds, newest first."""
    found = []
    for r in reversed(records):
        puzzles = {p["ply"] for p in r.get("puzzles", [])}
        found += [_example(r, e, puzzles) for e in r["errors"] if keep(r, e)]
    return found[:MAX_EXAMPLES]


def _average(values: list[float]) -> float:
    return sum(values) / len(values)


def _concentrated(records: list[dict]) -> list[dict]:
    """Kinds of mistakes that pile up in one phase or one opening."""
    # Each place has a name, the number of the player's moves in it per game
    # and a test of whether an error happened there.
    places = [
        (
            f"the {phase}",
            lambda r, p=phase: r["phases"].get(p, {}).get("moves", 0),
            lambda r, e, p=phase: e.get("phase") == p,
        )
        for phase in PHASES
    ]
    openings: dict[str, int] = {}
    for r in records:
        if side := _side(r):
            openings[side] = openings.get(side, 0) + 1
    places += [
        (
            f"the {side}",
            lambda r, s=side: _moves(r) if _side(r) == s else 0,
            lambda r, e, s=side: _side(r) == s,
        )
        for side, games in openings.items()
        if games >= MIN_GAMES
    ]

    total_moves = sum(_moves(r) for r in records)
    found = []
    for category, (verb, noun) in CATEGORY_WORDS.items():
        errors = [
            (r, e) for r in records for e in r["errors"] if e["category"] == category
        ]
        for name, moves_in, test in places:
            here = sum(test(r, e) for r, e in errors)
            moves_here = sum(moves_in(r) for r in records)
            moves_elsewhere = total_moves - moves_here
            if here < MIN_ERRORS or not moves_here or not moves_elsewhere:
                continue
            rate = here / moves_here
            rate_elsewhere = (len(errors) - here) / moves_elsewhere
            if rate < CONCENTRATION * rate_elsewhere:
                continue
            share = moves_here / total_moves
            keys = {(id(r), e["ply"]) for r, e in errors if test(r, e)}
            advice = CATEGORY_ADVICE[category]
            if here == len(errors):
                count = f"All {here} of your {noun}"
            else:
                count = f"{here} of your {len(errors)} {noun}"
            found.append(
                {
                    "kind": "concentrated",
                    "errors": keys,
                    "category": category,
                    "place": name,
                    "title": f"You {verb} in {name}",
                    "text": f"{count} came in {name}, which makes up "
                    f"{share:.0%} of your moves. "
                    f"{advice[0].upper()}{advice[1:]}.",
                    "ratio": rate / rate_elsewhere if rate_elsewhere else MAX_RATIO,
                    "examples": _examples(
                        records,
                        lambda r, e, c=category, t=test: e["category"] == c and t(r, e),
                    ),
                }
            )
    return found


def _worse(records: list[dict]) -> list[dict]:
    """Openings and colours in which the player loses clearly more."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in records:
        groups.setdefault(("color", r["color"].capitalize()), []).append(r)
        if side := _side(r):
            groups.setdefault(("opening", side), []).append(r)
    found = []
    for (kind, name), games in groups.items():
        members = {id(r) for r in games}
        others = [r for r in records if id(r) not in members]
        if len(games) < MIN_GAMES or len(others) < MIN_GAMES:
            continue
        acpl = _average([r["acpl"] for r in games])
        acpl_elsewhere = _average([r["acpl"] for r in others])
        if acpl < WORSE_RATIO * acpl_elsewhere or acpl - acpl_elsewhere < WORSE_CP:
            continue
        if kind == "color":
            title = f"You play worse with {name}"
            where = f"with {name}"
            elsewhere = "with the other colour"
        else:
            title = f"You play the {name} worse than your other openings"
            where = f"in the {name}"
            elsewhere = "in your other games"
        results = [r["result"] for r in games if r["result"]]
        score = (
            (results.count("win") + 0.5 * results.count("draw")) / len(results)
            if results
            else None
        )
        text = (
            f"Your average centipawn loss {where} is {acpl:.0f} over "
            f"{len(games)} games, against {acpl_elsewhere:.0f} {elsewhere}"
            + (f", and you score {score:.0%}." if score is not None else ".")
        )
        found.append(
            {
                "kind": "worse",
                "place": name,
                "title": title,
                "text": text,
                "ratio": acpl / acpl_elsewhere if acpl_elsewhere else MAX_RATIO,
                "examples": _examples(
                    records,
                    lambda r, e, m=members: id(r) in m,
                ),
            }
        )
    return found


def _clock(records: list[dict]) -> list[dict]:
    """Openings in which the player spends much more of the clock."""
    timed = [r for r in records if r.get("clock_used") is not None and _side(r)]
    groups: dict[str, list[dict]] = {}
    for r in timed:
        groups.setdefault(_side(r), []).append(r)
    found = []
    for name, games in groups.items():
        others = [r for r in timed if _side(r) != name]
        if len(games) < MIN_GAMES or len(others) < MIN_GAMES:
            continue
        used = _average([r["clock_used"] for r in games])
        used_elsewhere = _average([r["clock_used"] for r in others])
        if used - used_elsewhere < CLOCK_GAP or used < CLOCK_RATIO * used_elsewhere:
            continue
        thinks = sorted(
            (
                {
                    "id": r["id"],
                    "date": r["date"],
                    "opponent": r["opponent"],
                    "color": r["color"],
                    **t,
                }
                for r in games
                for t in r.get("thinks", [])
            ),
            key=lambda t: -t["seconds"],
        )
        found.append(
            {
                "kind": "clock",
                "place": name,
                "title": f"You spend a lot of time in the opening of the {name}",
                "text": f"By move 15 you have used {used:.0%} of your clock on "
                f"average in these {len(games)} games, against "
                f"{used_elsewhere:.0%} in your other openings. Learn its "
                "typical plans so the first moves come quickly.",
                "ratio": used / used_elsewhere if used_elsewhere else MAX_RATIO,
                "examples": [],
                "thinks": thinks[:MAX_EXAMPLES],
            }
        )
    return found


def find_weaknesses(records: list[dict]) -> list[dict]:
    """List the clearest recurring weaknesses in ``records``, strongest first.

    ``records`` come from :func:`chess_analyzer.stats.game_record`, oldest
    first. Each weakness has a ``kind`` (``concentrated``, ``worse`` or
    ``clock``), a ``title`` and a ``text`` in plain English, and the moves
    behind it as ``examples`` (newest first, with ``puzzle`` set when the
    position is one of the player's puzzles) or, for the clock, the longest
    ``thinks``.
    """
    found = _concentrated(records) + _worse(records) + _clock(records)
    found.sort(key=lambda w: -min(w["ratio"], MAX_RATIO))
    kept: list[dict] = []
    for w in found:
        # The same mistakes seen from two sides, such as the endgame and the
        # opening whose games those endgames came from, are one weakness.
        errors = w.pop("errors", None)
        if errors and any(
            k.get("category") == w["category"] and k["_errors"] >= errors for k in kept
        ):
            continue
        w["_errors"] = errors
        w["ratio"] = round(min(w["ratio"], MAX_RATIO), 1)
        kept.append(w)
    for w in kept:
        del w["_errors"]
    return kept[:MAX_WEAKNESSES]
