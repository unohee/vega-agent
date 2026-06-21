from __future__ import annotations


def test_db_path_defaults_to_vega_db(tmp_path, monkeypatch):
    from pipeline import data_paths

    monkeypatch.setenv("VEGA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("VEGA_DB_FILE", raising=False)
    data_paths.data_dir.cache_clear()

    assert data_paths.db_path() == tmp_path / "vega.db"
