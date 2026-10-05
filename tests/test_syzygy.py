from unittest.mock import MagicMock

import chess
import pytest
import requests

from chess_analyzer.syzygy import download, table_files

WDL = chess.Board.tbw_magic + b"table"


def test_table_files_cover_all_5_piece_endings():
    files = table_files()
    # 145 tables with up to five pieces, each with a WDL and a DTZ file.
    assert len(files) == 290
    assert "KQvK.rtbw" in files and "KRPvKR.rtbz" in files
    assert len(table_files(dtz=False)) == 145
    assert len(table_files(pieces=3, dtz=False)) == 5


def fake_session(responses):
    """A session whose GET returns ``responses[url]`` (bytes or an exception)."""
    session = MagicMock()

    def get(url, stream, timeout):
        body = responses.get(url, requests.HTTPError("404"))
        if isinstance(body, Exception):
            raise body
        response = MagicMock()
        response.__enter__.return_value.iter_content.return_value = [body]
        return response

    session.get.side_effect = get
    return session


def test_download_skips_existing_files(tmp_path):
    (tmp_path / "KPvK.rtbw").write_bytes(WDL)
    names = table_files(pieces=3, dtz=False)
    session = fake_session({f"https://a.test/tb/{n}": WDL for n in names})

    count = download(
        tmp_path,
        ["https://a.test/tb"],
        pieces=3,
        dtz=False,
        session=session,
        log=lambda _: None,
    )

    assert count == 4
    urls = [c.args[0] for c in session.get.call_args_list]
    assert "https://a.test/tb/KPvK.rtbw" not in urls
    assert (tmp_path / "KQvK.rtbw").read_bytes() == WDL
    assert not list(tmp_path.glob("*.part"))


def test_download_falls_back_to_the_next_mirror(tmp_path):
    names = table_files(pieces=3, dtz=False)
    responses = {f"https://b.test/{n}": WDL for n in names}
    # The first mirror serves an error page for one file and fails for others.
    responses["https://a.test/KQvK.rtbw"] = b"<html>Not found</html>"
    session = fake_session(responses)

    download(
        tmp_path,
        ["https://a.test", "https://b.test"],
        pieces=3,
        dtz=False,
        session=session,
        log=lambda _: None,
    )

    assert (tmp_path / "KQvK.rtbw").read_bytes() == WDL
    assert not list(tmp_path.glob("*.part"))


def test_download_fails_when_no_mirror_has_the_file(tmp_path):
    session = fake_session({})
    with pytest.raises(RuntimeError, match="KBvK.rtbw"):
        download(
            tmp_path,
            ["https://a.test"],
            pieces=3,
            dtz=False,
            session=session,
            log=lambda _: None,
        )


def test_download_fills_in_the_table_type(tmp_path):
    names = table_files(pieces=3)
    session = fake_session(
        {
            f"https://a.test/{'wdl' if n.endswith('w') else 'dtz'}/{n}": (
                WDL if n.endswith("w") else chess.Board.tbz_magic + b"table"
            )
            for n in names
        }
    )

    count = download(
        tmp_path,
        ["https://a.test/{type}"],
        pieces=3,
        session=session,
        log=lambda _: None,
    )

    assert count == 10
    assert (tmp_path / "KQvK.rtbz").read_bytes().startswith(chess.Board.tbz_magic)
