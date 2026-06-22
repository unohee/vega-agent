from __future__ import annotations


def test_db_path_defaults_to_agent_db(tmp_path, monkeypatch):
    # The distributed vega-agent build defaults to agent.db so it never collides
    # with a personal vega.db (reference_two_db_fork). Changing this default
    # silently orphans existing installs' data (B1 / INT-1682).
    from pipeline import data_paths

    monkeypatch.setenv("VEGA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("VEGA_DB_FILE", raising=False)
    data_paths.data_dir.cache_clear()

    assert data_paths.db_path() == tmp_path / "agent.db"
