"""Find trends and recurring weaknesses across a player's analysed games.

Works on analysis results as produced by
:func:`chess_analyzer.serialize.result_to_dict`, so the same code serves the
command line and the web app's stored analyses.
"""

from __future__ import annotations

from collections import Counter

from chess_analyzer.engine import ERRORS, FOUND
from chess_analyzer.insights import (
    CATEGORIES,
    CATEGORY_LABELS,
    IMPULSIVE,
    PHASES,
    POSITIONAL,
    TIME_FLAGS,
    TIME_TROUBLE,
    WASTED_TIME,
)
from chess_analyzer.plans import MISSED, PLAYED, UNSOUND, check_plans

# A trend needs at least this many games, and the average centipawn loss of
# the recent half must differ by this much from the older half.
TREND_GAMES = 4
TREND_CHANGE = 5
# Leaving book by this move means the opening needs work; staying in book
# past LATE_DEVIATION means the time is better spent on the middlegame.
EARLY_DEVIATION = 6
LATE_DEVIATION = 12
# Minimum mistakes before naming the most common kind, and moves per phase
# before comparing phases.
MIN_ERRORS = 3
MIN_PHASE_MOVES = 10
# A kind of mistake, or a time problem, is named when it accounts for at
# least this share of the mistakes and blunders.
NOTABLE_SHARE = 0.25
# Critical moments to name a share of found ones, and the most puzzles from
# them to list.
MIN_CRITICAL = 5
MAX_PUZZLES = 10
# The most moves to list for each kind of mistake.
MAX_EXAMPLES = 20
# A pawn break is named in the advice once it applied in this many games, and
# its score difference once there are this many games with and without it.
MIN_PLAN_GAMES = 2
PLAN_SCORE_GAP = 0.25
# Words that end the name of an opening family, as in "Sicilian Defense
# Najdorf Variation".
FAMILY_WORDS = ("Defense", "Defence", "Game", "Opening", "Gambit", "Attack", "System")

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


def opening_family(name: str) -> str:
    """Shorten an opening name to its family, e.g. ``Sicilian Defense``.

    Chess.com names openings down to the variation, which would put almost
    every game in a group of its own.
    """
    name = name.split(":")[0].strip()
    words = name.split()
    for i, word in enumerate(words[1:], start=1):
        if word in FAMILY_WORDS:
            return " ".join(words[: i + 1])
    return name


def _result_for(result: str, color: str) -> str | None:
    if result == "1/2-1/2":
        return "draw"
    if result in ("1-0", "0-1"):
        white_won = result == "1-0"
        return "win" if white_won == (color == "white") else "loss"
    return None


def game_record(
    result: dict, username: str, analysis_id: str | None = None
) -> dict | None:
    """Extract what the trend statistics need from one analysis result.

    Returns ``None`` if ``username`` did not play the game.
    """
    headers = result.get("headers", {})
    name = username.lower()
    if headers.get("White", "").lower() == name:
        color, opponent = "white", headers.get("Black")
    elif headers.get("Black", "").lower() == name:
        color, opponent = "black", headers.get("White")
    else:
        return None
    summary = result["summary"][color]
    opening = result.get("opening") or {}
    deviation = opening.get("deviation")
    left_book = None
    left_book_ply = None
    opponent_left_book = None
    if deviation:
        move = (deviation["ply"] + 1) // 2
        if deviation["color"] == color:
            left_book, left_book_ply = move, deviation["ply"]
        else:
            opponent_left_book = move
    date = headers.get("UTCDate") or headers.get("Date", "")
    moves = result.get("moves", [])
    critical = [
        {
            "ply": m["ply"],
            "label": m["label"],
            "fen": m["fen_before"],
            "best_san": m["best_san"],
            "best_uci": m["best_uci"],
            "found": m["classification"] in FOUND,
        }
        for ply in summary.get("critical") or []
        if 0 < ply <= len(moves) and (m := moves[ply - 1])
    ]
    errors = [
        {
            "ply": m["ply"],
            "label": m["label"],
            "classification": m["classification"],
            "category": m["category"],
            "best_san": m["best_san"],
        }
        for m in moves
        if m["color"] == color and m["classification"] in ERRORS and m.get("category")
    ]
    return {
        "id": analysis_id,
        "date": date.replace(".", "-") if "?" not in date else "",
        "utc_time": headers.get("UTCTime", ""),
        "color": color,
        "opponent": opponent,
        "result": _result_for(headers.get("Result", ""), color),
        "acpl": summary["acpl"],
        "opening": opening.get("name"),
        "opening_checked": opening.get("checked", False),
        "left_book": left_book,
        "left_book_ply": left_book_ply,
        "opponent_left_book": opponent_left_book,
        "link": headers.get("Link"),
        "categories": summary.get("categories") or {},
        "phases": summary.get("phases") or {},
        "time": summary.get("time"),
        "critical": critical,
        "errors": errors,
        "plans": check_plans(opening.get("name"), color, moves),
    }


def _trend(acpls: list[int]) -> dict | None:
    if len(acpls) < TREND_GAMES:
        return None
    half = len(acpls) // 2
    older = sum(acpls[:half]) / half
    recent = sum(acpls[-half:]) / half
    change = recent - older
    if change <= -TREND_CHANGE:
        direction = "improving"
    elif change >= TREND_CHANGE:
        direction = "worsening"
    else:
        direction = "steady"
    return {
        "direction": direction,
        "older": round(older),
        "recent": round(recent),
        "games": half,
    }


def _score(results: list[str]) -> float | None:
    if not results:
        return None
    return (results.count("win") + 0.5 * results.count("draw")) / len(results)


def _plans(games: list[dict]) -> list[dict]:
    """Count how often each pawn break of an opening was carried out."""
    plans: dict[str, dict] = {}
    for g in games:
        for check in g.get("plans") or []:
            plan = plans.setdefault(
                check["name"],
                {"name": check["name"], "goal": check["goal"], "games": 0}
                | {PLAYED: 0, UNSOUND: 0, MISSED: 0, "with": [], "without": []},
            )
            plan["games"] += 1
            if check["status"] in plan:
                plan[check["status"]] += 1
            if g["result"]:
                carried_out = check["status"] in (PLAYED, UNSOUND)
                plan["with" if carried_out else "without"].append(g["result"])
    for plan in plans.values():
        with_, without = plan.pop("with"), plan.pop("without")
        plan["score_with"], plan["games_with"] = _score(with_), len(with_)
        plan["score_without"], plan["games_without"] = _score(without), len(without)
    return list(plans.values())


def _openings(records: list[dict]) -> list[dict]:
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in records:
        if r["opening"] and r["opening_checked"]:
            key = (opening_family(r["opening"]), r["color"])
            groups.setdefault(key, []).append(r)
    openings = []
    for (name, color), games in groups.items():
        left = [g["left_book"] for g in games if g["left_book"] is not None]
        score = _score([g["result"] for g in games if g["result"]])
        average = sum(left) / len(left) if left else None
        if average is None:
            advice = None
        elif average <= EARLY_DEVIATION:
            advice = "study"
        elif average >= LATE_DEVIATION:
            advice = "middlegame"
        else:
            advice = None
        openings.append(
            {
                "name": name,
                "color": color,
                "games": len(games),
                "left_book": round(average, 1) if average is not None else None,
                "you_left": len(left),
                "opponent_left": sum(
                    g["opponent_left_book"] is not None for g in games
                ),
                "score": score,
                "acpl": round(sum(g["acpl"] for g in games) / len(games)),
                "advice": advice,
                "plans": _plans(games),
                # Newest first, to open each game where it left book.
                "game_list": [
                    {
                        k: g.get(k)
                        for k in ("id", "date", "opponent", "result", "acpl")
                        + ("left_book", "left_book_ply")
                    }
                    | {
                        "plans": [
                            {k: p[k] for k in ("name", "status", "ply", "label")}
                            for p in g.get("plans") or []
                        ]
                    }
                    for g in reversed(games)
                ],
            }
        )
    return sorted(openings, key=lambda o: (-o["games"], o["name"]))


def _phases(records: list[dict]) -> dict[str, dict]:
    totals: dict[str, list[float]] = {}
    for r in records:
        for phase, data in r["phases"].items():
            loss, moves = totals.setdefault(phase, [0.0, 0])
            totals[phase] = [loss + data["acpl"] * data["moves"], moves + data["moves"]]
    return {
        phase: {
            "acpl": round(totals[phase][0] / totals[phase][1]),
            "moves": totals[phase][1],
        }
        for phase in PHASES
        if phase in totals and totals[phase][1]
    }


def _time(records: list[dict]) -> dict | None:
    timed = [r["time"] for r in records if r["time"]]
    if not timed:
        return None
    moves = sum(t["moves"] for t in timed)
    return {
        "games": len(timed),
        "moves": moves,
        "average": round(sum(t["average"] * t["moves"] for t in timed) / moves, 1),
        "errors": sum(t["errors"] for t in timed),
        **{flag: sum(t.get(flag, 0) for t in timed) for flag in TIME_FLAGS},
    }


def _insights(stats: dict) -> list[str]:
    """Describe the player's clearest weaknesses in plain sentences."""
    lines = []
    trend = stats["trend"]
    if trend and trend["direction"] != "steady":
        verb = "fell" if trend["direction"] == "improving" else "rose"
        lines.append(
            f"Your average centipawn loss {verb} from {trend['older']} in your "
            f"earlier games to {trend['recent']} in your {trend['games']} most "
            "recent ones."
        )

    phases = {p: d for p, d in stats["phases"].items() if d["moves"] >= MIN_PHASE_MOVES}
    if len(phases) >= 2:
        worst = max(phases, key=lambda p: phases[p]["acpl"])
        others = [d["acpl"] for p, d in phases.items() if p != worst]
        if phases[worst]["acpl"] >= 1.5 * max(others) and phases[worst]["acpl"] >= 30:
            lines.append(
                f"You lose the most in the {worst} (average centipawn loss "
                f"{phases[worst]['acpl']}, against {max(others)} at most "
                f"elsewhere)."
            )

    categories = stats["categories"]
    total = sum(categories.values())
    if total >= MIN_ERRORS:
        # Positional errors are what is left when no tactic explains the
        # mistake, which is too vague to act on.
        named = [c for c in CATEGORIES if c != POSITIONAL]
        common = max(named, key=lambda c: categories.get(c, 0))
        share = categories.get(common, 0) / total
        if share >= NOTABLE_SHARE:
            lines.append(
                f"The most common kind of mistake ({share:.0%} of your mistakes "
                f'and blunders) is "{CATEGORY_LABELS[common]}": '
                f"{CATEGORY_ADVICE[common]}."
            )

    critical = stats["critical"]
    if critical["total"] >= MIN_CRITICAL:
        share = critical["found"] / critical["total"]
        if share < 0.5:
            lines.append(
                f"You found the only good move in {critical['found']} of "
                f"{critical['total']} critical moments: practise on these "
                "positions from your own games, and take more time when only "
                "one move holds."
            )

    time = stats["time"]
    if time and time["errors"] >= MIN_ERRORS:
        if time[TIME_TROUBLE] / time["errors"] >= NOTABLE_SHARE:
            lines.append(
                f"{time[TIME_TROUBLE]} of your {time['errors']} mistakes and "
                "blunders came with less than 10% of your time left: your "
                "problem is time management, not chess knowledge."
            )
        if time[IMPULSIVE] / time["errors"] >= NOTABLE_SHARE:
            lines.append(
                f"{time[IMPULSIVE]} of your {time['errors']} mistakes and "
                "blunders were played in under 3 seconds: slow down and "
                "calculate before moving."
            )
    if time and time[WASTED_TIME] >= 2:
        lines.append(
            f"You spent a long time on {time[WASTED_TIME]} obvious moves, such "
            "as recaptures and forced moves."
        )

    for opening in stats["openings"]:
        side = f"{opening['name']} as {opening['color']}"
        lines += _plan_insights(opening, side)
        if opening["you_left"] < 2 or opening["advice"] is None:
            continue
        if opening["advice"] == "study":
            lines.append(
                f"You leave book early in the {side} (move "
                f"{opening['left_book']:g} on average): study this opening."
            )
        else:
            breaks = " and ".join(p["name"] for p in opening.get("plans", []))
            lines.append(
                f"You know the {side} well (book until move "
                f"{opening['left_book']:g} on average): study its typical "
                "middlegame plans and pawn structures instead"
                + (f", such as the {breaks} breaks." if breaks else ".")
            )
    return lines


def _plan_insights(opening: dict, side: str) -> list[str]:
    """Name the pawn breaks the player keeps missing, or that win them games."""
    lines = []
    for plan in opening.get("plans", []):
        if plan["games"] < MIN_PLAN_GAMES:
            continue
        name = plan["name"]
        if plan[MISSED] >= MIN_PLAN_GAMES and plan[MISSED] / plan["games"] >= 0.5:
            lines.append(
                f"In the {side}, the engine wanted the {name} break in "
                f"{plan[MISSED]} of your {plan['games']} games but you never "
                f"played it. Its purpose is to {plan['goal']}: learn when it "
                "works instead of more opening moves."
            )
        elif plan[UNSOUND] >= MIN_PLAN_GAMES:
            lines.append(
                f"In the {side}, your {name} break was a mistake in "
                f"{plan[UNSOUND]} of {plan['games']} games: prepare it before "
                "playing it."
            )
        with_, without = plan["score_with"], plan["score_without"]
        if (
            plan["games_with"] >= MIN_PLAN_GAMES
            and plan["games_without"] >= MIN_PLAN_GAMES
            and with_ - without >= PLAN_SCORE_GAP
        ):
            lines.append(
                f"In the {side}, you score {with_:.0%} when you play {name} "
                f"and {without:.0%} when you don't."
            )
    return lines


def error_moves(
    records: list[dict], limit: int | None = MAX_EXAMPLES
) -> dict[str, list[dict]]:
    """List the mistakes, misses and blunders of each kind, newest first.

    ``records`` are sorted oldest first, as in :func:`player_stats`.
    """
    moves: dict[str, list[dict]] = {}
    for r in reversed(records):
        for e in r.get("errors", []):
            moves.setdefault(e["category"], []).append(
                {
                    "id": r["id"],
                    "date": r["date"],
                    "opponent": r["opponent"],
                    "color": r["color"],
                    **e,
                }
            )
    return {c: moves[c][:limit] for c in CATEGORIES if c in moves}


def puzzles(records: list[dict], limit: int | None = MAX_PUZZLES) -> list[dict]:
    """Turn the critical moments of the newest games into puzzles.

    Each puzzle is the position before the critical move, with the player to
    move; the solution is the engine's best move. ``records`` are sorted
    oldest first, as in :func:`player_stats`.
    """
    items = []
    for r in reversed(records):
        for c in r.get("critical", []):
            items.append(
                {
                    "id": r["id"],
                    "date": r["date"],
                    "opponent": r["opponent"],
                    "color": r["color"],
                    "link": r["link"],
                    **c,
                }
            )
    return items[:limit]


def player_stats(records: list[dict]) -> dict:
    """Combine game records from :func:`game_record` into overall statistics.

    Returns
    -------
    dict
        ``games`` (oldest first, for the trendline), ``acpl``, ``trend``,
        ``openings`` (each with its games and pawn breaks), ``categories``,
        ``category_moves`` (the moves of each kind of mistake), ``phases``, ``time``, ``critical``
        (how many critical moments the player met and found), ``puzzles``
        (critical moments from the most recent games, newest first) and
        ``insights``, a list of sentences naming the clearest weaknesses.
    """
    records = sorted(records, key=lambda r: (r["date"], r["utc_time"]))
    acpls = [r["acpl"] for r in records]
    categories: Counter = Counter()
    for r in records:
        categories.update(r["categories"])
    stats = {
        "games": [
            {
                k: r[k]
                for k in ("id", "date", "color", "opponent", "result", "acpl")
                + ("opening", "link")
            }
            for r in records
        ],
        "acpl": round(sum(acpls) / len(acpls)) if acpls else None,
        "trend": _trend(acpls),
        "openings": _openings(records),
        "categories": {c: categories[c] for c in CATEGORIES if categories[c]},
        "category_moves": error_moves(records),
        "phases": _phases(records),
        "time": _time(records),
        "critical": {
            "total": sum(len(r.get("critical", [])) for r in records),
            "found": sum(c["found"] for r in records for c in r.get("critical", [])),
        },
        "puzzles": puzzles(records),
    }
    stats["insights"] = _insights(stats)
    return stats
