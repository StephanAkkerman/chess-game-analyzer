from unittest.mock import MagicMock

from chess_analyzer.coach import Coach, names_only
from chess_analyzer.report import format_player_stats
from chess_analyzer.stats import game_record, player_stats
from chess_analyzer.weaknesses import find_weaknesses


def move(ply, phase="middlegame", classification="good", category="", **extra):
    return {
        "ply": ply,
        "color": "white" if ply % 2 else "black",
        "label": f"{(ply + 1) // 2}.Nf3",
        "classification": classification,
        "category": category,
        "phase": phase,
        "best_san": "d4",
        "best_uci": "d2d4",
        "uci": "g1f3",
        "fen_before": f"8/8/8/8/8/8/8/K6k w - - 0 {ply}",
        **extra,
    }


def game(
    date,
    acpl=40,
    opening="Sicilian Defense",
    errors=(),
    spent=None,
    time_control="600",
):
    """A game of alice's as White with 20 moves each in the middlegame and
    endgame; ``errors`` are (ply, phase, category) of her mistakes."""
    moves = [move(p, "middlegame" if p <= 40 else "endgame") for p in range(1, 81)]
    for ply, phase, category in errors:
        moves[ply - 1] = move(ply, phase, "mistake", category)
    if spent is not None:
        for m in moves:
            m["time_spent"] = spent if m["ply"] <= 30 else 5.0
    summary = {
        "acpl": acpl,
        "categories": {},
        "phases": {
            "middlegame": {"acpl": acpl, "moves": 20},
            "endgame": {"acpl": acpl, "moves": 20},
        },
        "time": None,
    }
    return {
        "headers": {
            "White": "alice",
            "Black": "bob",
            "Result": "1-0",
            "Date": date,
            "TimeControl": time_control,
        },
        "opening": {"name": opening, "checked": True, "deviation": None},
        "moves": moves,
        "summary": {"white": summary, "black": summary},
    }


def records(*games):
    return [game_record(g, "alice", str(i)) for i, g in enumerate(games)]


def test_mistakes_that_pile_up_in_the_endgame():
    endgame_tactics = [
        (41, "endgame", "missed_tactic"),
        (43, "endgame", "missed_tactic"),
    ]
    stats = player_stats(
        records(
            game("2026.10.01", errors=endgame_tactics),
            game("2026.10.02", errors=[(45, "endgame", "missed_tactic")]),
            game("2026.10.03", errors=[(11, "middlegame", "hung_piece")]),
        )
    )
    weakness = stats["weaknesses"][0]
    assert weakness["title"] == "You miss tactics in the endgame"
    assert weakness["text"].startswith(
        "All 3 of your missed tactics came in the endgame, which makes up 50% of "
        "your moves."
    )
    # The moves behind it, newest game first, are puzzles to practise.
    assert [(e["id"], e["ply"], e["puzzle"]) for e in weakness["examples"]] == [
        ("1", 45, True),
        ("0", 41, True),
        ("0", 43, True),
    ]
    # One hung piece is no pattern.
    assert not any("hang" in w["title"] for w in stats["weaknesses"])

    report = format_player_stats(stats, "alice")
    assert "Recurring weaknesses" in report
    assert "You miss tactics in the endgame." in report


def test_spread_out_mistakes_are_no_weakness():
    spread = [(11, "middlegame", "missed_tactic"), (41, "endgame", "missed_tactic")]
    assert (
        find_weaknesses(
            records(*(game(f"2026.10.0{i}", errors=spread) for i in range(1, 4)))
        )
        == []
    )


def test_worse_opening_and_slow_opening():
    stats = player_stats(
        records(
            *(game(f"2026.10.0{i}", acpl=30, spent=6.0) for i in range(1, 4)),
            *(
                game(f"2026.10.1{i}", acpl=80, opening="Caro-Kann Defense", spent=15.0)
                for i in range(1, 4)
            ),
            # Daily games have no clock to compare.
            game("2026.10.20", opening="Caro-Kann Defense", time_control="1/86400"),
        )
    )
    titles = [w["title"] for w in stats["weaknesses"]]
    assert (
        "You play the Caro-Kann Defense as White worse than your other openings"
        in titles
    )
    clock = next(w for w in stats["weaknesses"] if w["kind"] == "clock")
    assert clock["title"] == (
        "You spend a lot of time in the opening of the Caro-Kann Defense as White"
    )
    assert (
        "used 38% of your clock on average in these 3 games, against 15%"
        in clock["text"]
    )
    assert clock["thinks"][0]["seconds"] == 15


def test_names_only():
    source = "You miss tactics in the Caro-Kann Defense: 3 of your 4."
    assert names_only("Work on the Caro-Kann Defense. Three of 4 is a lot.", source)
    assert not names_only("Study the Sicilian Defense as well.", source)
    assert not names_only("You missed 7 tactics.", source)


def test_coach_rewords_weaknesses():
    session = MagicMock()
    answer = {"choices": [{"message": {"content": "Start with endgame tactics."}}]}
    session.post.return_value.json.return_value = answer
    coach = Coach("http://llm/v1", "tiny", session=session)
    weaknesses = [{"title": "You miss tactics in the endgame", "text": "3 of your 3."}]
    assert coach.reword_weaknesses(weaknesses) == "Start with endgame tactics."
    assert coach.reword_weaknesses([]) is None

    answer["choices"][0]["message"]["content"] = "Study the Najdorf Variation."
    assert coach.reword_weaknesses(weaknesses) is None


def test_same_mistakes_count_once():
    tactics = [(41, "endgame", "missed_tactic"), (43, "endgame", "missed_tactic")]
    weaknesses = find_weaknesses(
        records(
            *(game(f"2026.10.0{i}") for i in range(1, 4)),
            *(
                game(f"2026.10.1{i}", opening="Caro-Kann Defense", errors=tactics)
                for i in range(1, 4)
            ),
        )
    )
    # Those endgames all came from Caro-Kann games: one weakness, not two.
    assert [w["title"] for w in weaknesses] == ["You miss tactics in the endgame"]
