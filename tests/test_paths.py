from pathlib import Path

from hyprschedule import paths


def test_functions_with_xdg_env_vars_set(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))

    assert paths.config_home() == tmp_path / "cfg"
    assert paths.data_home() == tmp_path / "data"
    assert paths.runtime_dir() == tmp_path / "run" / "hyprschedule"
    assert paths.config_dir() == tmp_path / "cfg" / "hyprschedule"
    assert paths.data_dir() == tmp_path / "data" / "hyprschedule"
    assert paths.config_file() == tmp_path / "cfg" / "hyprschedule" / "config.toml"
    assert paths.database_path() == tmp_path / "data" / "hyprschedule" / "schedule.db"


def test_functions_with_xdg_env_vars_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert paths.config_home() == tmp_path / ".config"
    assert paths.data_home() == tmp_path / ".local" / "share"
    assert paths.runtime_dir() == Path("/tmp") / "hyprschedule"
    assert paths.config_dir() == tmp_path / ".config" / "hyprschedule"
    assert paths.data_dir() == tmp_path / ".local" / "share" / "hyprschedule"
    assert paths.config_file() == tmp_path / ".config" / "hyprschedule" / "config.toml"
    assert paths.database_path() == tmp_path / ".local" / "share" / "hyprschedule" / "schedule.db"


def test_relative_derivations(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    assert paths.config_dir() == paths.config_home() / "hyprschedule"
    assert paths.data_dir() == paths.data_home() / "hyprschedule"
    assert paths.config_file() == paths.config_dir() / "config.toml"
    assert paths.database_path() == paths.data_dir() / "schedule.db"