from unittest.mock import MagicMock

from chess_analyzer.syzygy import download, table_files


def test_table_files_cover_all_5_piece_endings():
    files = table_files()
    # 145 tables with up to five pieces, each with a WDL and a DTZ file.
    assert len(files) == 290
    assert "KQvK.rtbw" in files and "KRPvKR.rtbz" in files
    assert len(table_files(dtz=False)) == 145
    assert len(table_files(pieces=3, dtz=False)) == 5


def test_download_skips_existing_files(tmp_path):
    (tmp_path / "KPvK.rtbw").write_bytes(b"already here")
    session = MagicMock()
    response = session.get.return_value.__enter__.return_value
    response.iter_content.return_value = [b"table"]

    count = download(
        tmp_path,
        url="https://example.org/tb",
        pieces=3,
        dtz=False,
        session=session,
        log=lambda _: None,
    )

    assert count == 4
    urls = [c.args[0] for c in session.get.call_args_list]
    assert "https://example.org/tb/KQvK.rtbw" in urls
    assert "https://example.org/tb/KPvK.rtbw" not in urls
    assert (tmp_path / "KQvK.rtbw").read_bytes() == b"table"
    assert not list(tmp_path.glob("*.part"))
