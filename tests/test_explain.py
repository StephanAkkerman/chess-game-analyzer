from unittest.mock import MagicMock

import chess
import requests

from chess_analyzer.coach import Coach, mentions_only
from chess_analyzer.explain import explain_move, line_outcome, move_facts


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
