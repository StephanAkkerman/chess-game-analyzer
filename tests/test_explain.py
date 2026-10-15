from unittest.mock import MagicMock

import chess
import requests

from chess_analyzer.coach import Coach, mentions_only
from chess_analyzer.explain import explain_move, how_to_avoid, line_outcome, move_facts


def move_dict(board, uci, classification, eval_before, eval_after, **extra):
    move = chess.Move.from_uci(uci)
    return {
        "fen_before": board.fen(),
        "uci": uci,
        "san": board.san(move),
        "label": board.san(move),
        "classification": classification,
        "eval_before": eval_before,
        "eval_after": eval_after,
        **extra,
    }


def kinds(board, uci, **kwargs):
    return [f.kind for f in move_facts(board, chess.Move.from_uci(uci), **kwargs)]


def test_facts_of_common_moves():
    board = chess.Board()
    assert kinds(board, "g1f3") == ["develops", "centre"]
    assert kinds(board, "e2e4") == ["centre"]
    # A knight fork with check.
    board = chess.Board("r3k3/8/8/1N6/8/8/8/4K3 w - - 0 30")
    facts = move_facts(board, chess.Move.from_uci("b5c7"))
    assert facts[0].kind == "fork"
    assert facts[0].text == "gives check and also attacks the rook on a8"
    # Castling.
    board = chess.Board("r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 20")
    assert kinds(board, "e1g1")[0] == "castles"
    assert "king" in kinds(board, "e1f1")


def test_captures_and_hanging_pieces():
    # Taking an undefended knight.
    board = chess.Board("4k3/8/8/3n4/8/8/8/3QK3 w - - 0 30")
    assert kinds(board, "d1d5")[0] == "wins"
    # Taking back on the square where the opponent just captured.
    assert kinds(board, "d1d5", recapture_on=chess.D5)[0] == "recapture"
    # 3.Ng5 in the Italian leaves the knight to the queen.
    board = chess.Board()
    for san in ("e4", "e5", "Nf3", "Nc6"):
        board.push_san(san)
    assert "hangs" in kinds(board, "f3g5")


def test_explains_a_blunder_and_the_better_move():
    board = chess.Board()
    for san in ("e4", "e5", "Bc4", "Nc6", "Qh5"):
        board.push_san(san)
    move = move_dict(
        board,
        "g8f6",
        "blunder",
        -20,
        9_999,
        label="3...Nf6",
        best_uci="g7g6",
        reply_uci="h5f7",
        best_line=["g7g6", "h5f3", "g8f6"],
    )
    explanation = explain_move(move)
    text = explanation["text"]
    assert text.startswith("3...Nf6 is a blunder: it turns a roughly equal")
    assert "White answers Qxf7#, which delivers checkmate." in text
    assert "Better was g6, which attacks the queen on h5." in text
    assert {"about": "reply", "kind": "mate", "text": "delivers checkmate"} in (
        explanation["facts"]
    )

    hidden = explain_move(move, hide_best=True)
    assert "g6" not in hidden["text"]
    assert all(f["about"] != "best" for f in hidden["facts"])
    assert "try to find it" in hidden["text"]


def test_explains_what_a_quiet_reply_threatens_and_how_the_better_move_stops_it():
    # 23.Kh1 lets Rf8 threaten Rf1#: the bishop on d6 covers h2. With 23.g3
    # the king gets h2 back.
    board = chess.Board("r5k1/pp2q1p1/3b3p/7Q/6R1/2N4P/PP4P1/6K1 w - - 0 23")
    move = move_dict(
        board,
        "g1h1",
        "mistake",
        -80,
        -350,
        label="23.Kh1",
        best_uci="g2g3",
        reply_uci="a8f8",
    )
    explanation = explain_move(move)
    text = explanation["text"]
    assert "Black answers Rf8, which threatens mate with Rf1#." in text
    assert (
        "Better was g3: after it, Rf8 no longer threatens mate with Rf1#, because "
        "the king could escape to h2." in text
    )
    assert {
        "about": "best",
        "kind": "prevents",
        "text": ("after g3, Rf8 no longer threatens mate with Rf1#"),
    } in explanation["facts"]


def test_explains_a_king_move_into_a_pin():
    # 23.Kh1 puts the king on the long diagonal behind the knight on f3, so
    # Rf8 wins it; 23.Kh2 keeps the knight free.
    board = chess.Board("r5k1/1b4p1/1p5p/p2q4/3P4/P1P2N1P/1P2QP2/6K1 w - - 0 23")
    move = move_dict(
        board,
        "g1h1",
        "mistake",
        -80,
        -350,
        label="23.Kh1",
        best_uci="g1h2",
        reply_uci="a8f8",
        reply_line=["a8f8", "a3a4", "f8f3", "h1g1"],
    )
    explanation = explain_move(move)
    text = explanation["text"]
    assert "It lets the queen on d5 pin the knight on f3 to the king." in text
    assert "Black answers Rf8, which puts pressure on the knight on f3" in text
    assert "25. Kg1 Black is 3 pawns up in material." in text
    assert text.endswith("Better was Kh2: after it, the knight on f3 is not pinned.")

    # The line the text spells out comes with its positions, to play it out
    # on the board.
    (line,) = explanation["lines"]
    assert line["san"] in text
    assert line["san"].startswith("23...Rf8 24. a4 Rxf3")
    position = board.copy()
    position.push_uci("g1h1")
    assert line["fen"] == position.fen()
    assert [m["uci"] for m in line["moves"]] == move["reply_line"]
    assert [m["san"] for m in line["moves"]] == ["Rf8", "a4", "Rxf3", "Kg1"]
    for step in line["moves"]:
        position.push_uci(step["uci"])
        assert step["fen"] == position.fen()


def test_line_outcome_ignores_a_half_finished_exchange():
    board = chess.Board("4k3/8/8/3n4/4P3/8/8/3QK3 w - - 0 30")
    # A line that stops right after a capture may stop before the recapture,
    # so the capture is not counted yet.
    _, gained, mated = line_outcome(board, ["e4d5"], chess.WHITE)
    assert (gained, mated) == (0, False)
    # After Qxd5 Black does not take back: the knight is won.
    san, gained, _ = line_outcome(board, ["d1d5", "e8e7"], chess.WHITE)
    assert san == "30. Qxd5 Ke7"
    assert gained == 3
    # Illegal moves end the line.
    assert line_outcome(board, ["a1a8"], chess.WHITE) == ("", 0, False)


def test_coach_rewords_and_validates():
    session = MagicMock()
    answer = {"choices": [{"message": {"content": "Nf6 drops f7; g6 holds."}}]}
    session.post.return_value.json.return_value = answer
    coach = Coach("http://llm/v1/", "tiny", api_key="k", session=session)
    facts = [{"about": "best", "kind": "threat", "text": "attacks the queen on h5"}]
    assert coach.reword("Nf6 loses to Qxf7#. Better was g6.", facts) == (
        "Nf6 drops f7; g6 holds."
    )
    url = session.post.call_args.args[0]
    assert url == "http://llm/v1/chat/completions"
    assert session.post.call_args.kwargs["headers"] == {"Authorization": "Bearer k"}

    # A square that is not in the facts is made up: the answer is dropped.
    answer["choices"][0]["message"]["content"] = "Play Nd4 instead."
    assert coach.reword("Nf6 loses to Qxf7#. Better was g6.", facts) is None

    session.post.side_effect = requests.ConnectionError("down")
    assert coach.reword("Nf6 loses.", facts) is None


def test_mentions_only():
    assert mentions_only("Castle with O-O, then Re1.", "O-O and Re1 on e1")
    assert not mentions_only("Bxf7+ wins.", "Bc4 eyes f7")


def played(fens_and_moves, classification="good", **extra):
    """Move dictionaries for ``(fen, uci)`` pairs, in order."""
    moves = []
    for fen, uci in fens_and_moves:
        board = chess.Board(fen)
        move = move_dict(board, uci, classification, None, None)
        move["color"] = "white" if board.turn == chess.WHITE else "black"
        moves.append(move)
    moves[-1].update(extra)
    return moves


def blunder(fen, uci, reply, history=()):
    board = chess.Board(fen)
    move = move_dict(board, uci, "blunder", 0, -500, reply_uci=reply)
    move["color"] = "white" if board.turn == chess.WHITE else "black"
    return how_to_avoid(move, history[-1] if history else None, list(history))


def test_how_to_avoid_a_fork_the_move_walks_into():
    # Qc3 puts the queen a knight's jump from e2, like the king on g1.
    avoid = blunder("6k1/8/8/8/3n4/8/8/Q5K1 w - - 0 1", "a1c3", "d4e2")
    assert avoid["kind"] == "fork"
    assert avoid["text"].startswith(
        "Qc3 put the queen on c3, where it can be forked from e2."
    )
    assert "knight's jump" in avoid["text"]


def test_how_to_avoid_names_the_earlier_move_that_allowed_it():
    start = chess.Board("6k1/8/8/8/3n4/8/7P/Q5K1 w - - 0 1")
    history = [(start.fen(), "a1c3")]
    start.push_uci("a1c3")
    history.append((start.fen(), "g8f8"))
    start.push_uci("g8f8")
    avoid = blunder(start.fen(), "h2h3", "d4e2", played(history))
    assert avoid["text"].startswith(
        "The fork was already possible after your earlier Qc3, the move that "
        "allowed it, and h3 did nothing about it."
    )


def test_how_to_avoid_a_threat_the_opponent_set_up():
    board = chess.Board()
    history = []
    for san in ("e4", "e5", "Nf3", "Nc6", "Nc3", "Nf6", "Nb5"):
        history.append((board.fen(), board.parse_san(san).uci()))
        board.push_san(san)
    avoid = blunder(board.fen(), "a7a6", "b5c7", played(history))
    assert avoid["text"].startswith(
        "White's last move Nb5 set up the fork, and a6 did nothing about it."
    )


def test_how_to_avoid_pins_skewers_and_mates():
    # 3...d6 opens the diagonal from b5 to the king.
    board = chess.Board()
    for san in ("e4", "e5", "Nf3", "Nc6", "Nc3"):
        board.push_san(san)
    avoid = blunder(board.fen(), "d7d6", "f1b5")
    assert avoid["kind"] == "pin"
    assert avoid["text"].startswith(
        "d6 moved the pawn off d7, opening the line to your king."
    )
    # Ke4 walks onto the e-file in front of the queen.
    avoid = blunder("r5k1/8/8/8/3K4/8/4Q3/8 w - - 0 1", "d4e4", "a8e8")
    assert avoid["kind"] == "skewer"
    assert avoid["text"].startswith("Ke4 put the king on e4 on one line with the queen")
    # Leaving the back rank to the rook.
    avoid = blunder("3r2k1/5ppp/8/8/8/8/5PPP/R5K1 w - - 0 1", "a1a7", "d8d1")
    assert avoid["kind"] == "mate"
    assert avoid["text"].startswith(
        "Ra7 stopped guarding d1, the square the mate comes from. Give your "
        "king an escape square"
    )
    # A piece left where it can be taken.
    avoid = blunder(
        "rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2",
        "d8g5",
        "f3g5",
    )
    assert avoid["kind"] == "wins"
    assert avoid["text"].startswith("Qg5 put the queen on g5, where it can be taken.")


def test_how_to_avoid_only_explains_tactics_after_mistakes():
    board = chess.Board()
    move = move_dict(board, "e2e4", "best", 0, 30, reply_uci="e7e5")
    assert how_to_avoid(move) is None
    assert explain_move(move)["avoid"] == ""
