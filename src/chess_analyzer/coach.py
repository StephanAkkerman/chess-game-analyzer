"""Reword explanations with a language model, like a chess coach would.

:mod:`chess_analyzer.explain` works out what a move does, and
:mod:`chess_analyzer.weaknesses` what keeps going wrong across games, and
both say it with fixed sentence patterns. This module optionally hands those
facts to a small language model that turns them into a friendlier paragraph. The model never
sees the board, only the facts, and it is told to add nothing: language
models are poor at chess but good at wording.

Any server with an OpenAI-compatible chat completions API works, such as
`Ollama <https://ollama.com>`_ on the Raspberry Pi itself (``qwen2.5:1.5b``
fits in about 1 GB) or a hosted API. When the model is unavailable, slow or
mentions a square or move that is not in the facts, the plain explanation
is used instead; for weaknesses, also when it mentions a number or a name
(such as an opening) that is not in the facts.
"""

from __future__ import annotations

import logging
import re
import threading

import requests

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a friendly chess coach explaining a move from the student's own "
    "game. You get an explanation built from facts about the position. "
    "Rewrite it as one short paragraph (at most four sentences) in plain "
    "English, explaining why the move is good or bad and what the idea "
    "behind the better move is. Use only the given facts: do not mention any "
    "move, square, piece or evaluation that is not in them, and do not "
    "invent variations. Do not use headings, lists or markdown."
)
WEAKNESSES_PROMPT = (
    "You are a friendly chess coach. You get a list of weaknesses that keep "
    "coming back in the student's own games, with the numbers behind them. "
    "Write one short paragraph (at most five sentences) in plain English, "
    "speaking to the student as 'you': say what to work on first and why, "
    "and how to practise it. Use only the given facts: do not mention any "
    "opening, number, move or square that is not in them. Do not use "
    "headings, lists or markdown."
)
# Squares and moves in SAN (Nf3, exd5, O-O, e8=Q); the model may only use
# those that appear in the facts.
SQUARE = re.compile(r"[a-h][1-8]")
SAN = re.compile(r"\b(?:[KQRBN][a-h]?[1-8]?x?[a-h][1-8]|[a-h]x[a-h][1-8]|O-O(?:-O)?)")
NUMBER = re.compile(r"\d+(?:\.\d+)?")
SENTENCE_END = re.compile(r"(?<=[.!?:;])\s+")
WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")
MAX_CHARS = 900


def mentions_only(text: str, source: str) -> bool:
    """Return whether every square and move in ``text`` also occurs in ``source``."""
    for pattern in (SQUARE, SAN):
        if not set(pattern.findall(text)) <= set(pattern.findall(source)):
            return False
    return True


def names_only(text: str, source: str) -> bool:
    """Return whether every number and name in ``text`` also occurs in ``source``.

    A name is a capitalised word that does not start a sentence, such as an
    opening: a model that makes one up has invented a fact.
    """
    if not set(NUMBER.findall(text)) <= set(NUMBER.findall(source)):
        return False
    known = set(WORD.findall(source)) | {"I"}
    for sentence in SENTENCE_END.split(text):
        for word in WORD.findall(sentence)[1:]:
            if word[0].isupper() and word not in known:
                return False
    return True


class Coach:
    """Client for an OpenAI-compatible chat completions endpoint.

    Parameters
    ----------
    url : str
        Base URL of the API, e.g. ``http://ollama:11434/v1``.
    model : str
        Model name, e.g. ``qwen2.5:1.5b``.
    api_key : str, optional
        Bearer token, for hosted APIs.
    timeout : float
        Seconds to wait for an answer. A small model on a Raspberry Pi needs
        a while; the plain explanation is used when it takes longer.
    """

    def __init__(
        self,
        url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = 60.0,
        session: requests.Session | None = None,
    ) -> None:
        self.url = url.rstrip("/") + "/chat/completions"
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.session = session or requests.Session()
        # A Raspberry Pi can run one generation at a time at a useful speed.
        self._lock = threading.Lock()

    def reword(self, explanation: str, facts: list[dict]) -> str | None:
        """Reword a move's ``explanation``, or return ``None`` if that fails."""
        if not explanation:
            return None
        lines = [explanation, "", "Facts:"]
        lines += [f"- {f['about']}: {f['text']}" for f in facts]
        source = "\n".join(lines)
        text = self._complete(SYSTEM_PROMPT, source, max_tokens=220)
        if text is None or not mentions_only(text, source):
            log.info("Coach answer rejected: %r", (text or "")[:200])
            return None
        return text

    def reword_weaknesses(self, weaknesses: list[dict]) -> str | None:
        """Sum up a player's ``weaknesses`` as advice, or return ``None``.

        ``weaknesses`` come from :func:`chess_analyzer.weaknesses.find_weaknesses`.
        """
        if not weaknesses:
            return None
        source = "\n".join(f"- {w['title']}. {w['text']}" for w in weaknesses)
        text = self._complete(WEAKNESSES_PROMPT, source, max_tokens=260)
        if (
            text is None
            or not mentions_only(text, source)
            or not names_only(text, source)
        ):
            log.info("Coach answer rejected: %r", (text or "")[:200])
            return None
        return text

    def _complete(self, system: str, source: str, max_tokens: int) -> str | None:
        """Ask the model, returning its answer on one line or ``None``."""
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": source},
            ],
            "temperature": 0.3,
            "max_tokens": max_tokens,
            "stream": False,
        }
        try:
            with self._lock:
                response = self.session.post(
                    self.url, json=body, headers=headers, timeout=self.timeout
                )
            response.raise_for_status()
            text = response.json()["choices"][0]["message"]["content"]
        except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
            log.warning("Coach model failed: %s", exc)
            return None
        text = " ".join(str(text).split())
        if not text or len(text) > MAX_CHARS:
            return None
        return text
