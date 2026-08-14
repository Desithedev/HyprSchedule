import tomllib

import pytest

from hyprschedule.config import ConfigError, DEFAULT_CONFIG, load_config


def test_default_config_when_file_missing(tmp_path):
    cfg = load_config(tmp_path / "does-not-exist.toml")

    assert cfg.timezone == "Asia/Ho_Chi_Minh"
    assert cfg.schedule.day_start == "06:00"
    assert cfg.schedule.day_end == "23:00"
    assert cfg.schedule.min_free_minutes == 15
    assert cfg.widget.max_events == 6
    assert cfg.widget.show_free_time is True
    assert cfg.widget.show_tomorrow_count is True
    assert cfg.widget.refresh_seconds == 30
    assert cfg.lockscreen.max_events == 2
    assert cfg.lockscreen.show_private is False
    assert cfg.lockscreen.show_tomorrow_count is True
    assert cfg.notification.default_reminders == [15, 5, 0]
    assert cfg.notification.sound is True
    assert cfg.notification.sound_file == ""
    assert cfg.notification.missed_event_window_minutes == 30
    assert cfg.recurrence.skip_classes_on_holidays is True


def test_default_config_string_parses_to_defaults():
    parsed = tomllib.loads(DEFAULT_CONFIG)

    assert parsed["timezone"] == "Asia/Ho_Chi_Minh"
    assert parsed["schedule"]["day_start"] == "06:00"
    assert parsed["schedule"]["day_end"] == "23:00"
    assert parsed["schedule"]["min_free_minutes"] == 15
    assert parsed["widget"]["max_events"] == 6
    assert parsed["widget"]["show_free_time"] is True
    assert parsed["widget"]["show_tomorrow_count"] is True
    assert parsed["widget"]["refresh_seconds"] == 30
    assert parsed["lockscreen"]["max_events"] == 2
    assert parsed["lockscreen"]["show_private"] is False
    assert parsed["lockscreen"]["show_tomorrow_count"] is True
    assert parsed["notification"]["default_reminders"] == [15, 5, 0]
    assert parsed["notification"]["sound"] is True
    assert parsed["notification"]["sound_file"] == ""
    assert parsed["notification"]["missed_event_window_minutes"] == 30
    assert parsed["recurrence"]["skip_classes_on_holidays"] is True


def test_partial_config_overlays_defaults(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('timezone = "America/New_York"\n\n[widget]\nmax_events = 3\n')

    cfg = load_config(path)

    assert cfg.timezone == "America/New_York"
    assert cfg.widget.max_events == 3
    assert cfg.schedule.day_start == "06:00"
    assert cfg.schedule.day_end == "23:00"
    assert cfg.widget.show_free_time is True
    assert cfg.widget.refresh_seconds == 30
    assert cfg.lockscreen.max_events == 2
    assert cfg.lockscreen.show_private is False
    assert cfg.lockscreen.show_tomorrow_count is True
    assert cfg.notification.default_reminders == [15, 5, 0]
    assert cfg.recurrence.skip_classes_on_holidays is True


def test_invalid_toml_raises_config_error_with_path(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("this is = = not toml")

    with pytest.raises(ConfigError) as excinfo:
        load_config(path)

    assert str(path) in str(excinfo.value)


def test_invalid_timezone_raises_config_error(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('timezone = "Not/AZone"\n')

    with pytest.raises(ConfigError):
        load_config(path)


@pytest.mark.parametrize("bad_start", ["25:00", "24:00", "12:60", "7am"])
def test_invalid_day_start_raises_config_error(tmp_path, bad_start):
    path = tmp_path / "config.toml"
    path.write_text(f'[schedule]\nday_start = "{bad_start}"\n')

    with pytest.raises(ConfigError):
        load_config(path)


@pytest.mark.parametrize("bad_end", ["25:00", "24:00", "12:60", "7pm"])
def test_invalid_day_end_raises_config_error(tmp_path, bad_end):
    path = tmp_path / "config.toml"
    path.write_text(f'[schedule]\nday_end = "{bad_end}"\n')

    with pytest.raises(ConfigError):
        load_config(path)


@pytest.mark.parametrize(
    "bad_reminders",
    [
        "[15, -5]",
        "[15, '5']",
        "[1.5, 2]",
        "15",
    ],
)
def test_invalid_default_reminders_raises_config_error(tmp_path, bad_reminders):
    path = tmp_path / "config.toml"
    path.write_text(f"[notification]\ndefault_reminders = {bad_reminders}\n")

    with pytest.raises(ConfigError):
        load_config(path)


@pytest.mark.parametrize("value", ["0", "-1", "1.5", '"three"'])
def test_max_events_must_be_positive_int(tmp_path, value):
    path = tmp_path / "config.toml"
    path.write_text(f"[widget]\nmax_events = {value}\n")

    with pytest.raises(ConfigError):
        load_config(path)


@pytest.mark.parametrize("value", ["-1", "-30", "1.5", '"ten"'])
def test_min_free_minutes_must_be_non_negative_int(tmp_path, value):
    path = tmp_path / "config.toml"
    path.write_text(f"[schedule]\nmin_free_minutes = {value}\n")

    with pytest.raises(ConfigError):
        load_config(path)


def test_min_free_minutes_override(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[schedule]\nmin_free_minutes = 5\n")

    assert load_config(path).schedule.min_free_minutes == 5


def test_min_free_minutes_zero_allows_any_gap(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[schedule]\nmin_free_minutes = 0\n")

    assert load_config(path).schedule.min_free_minutes == 0


@pytest.mark.parametrize("value", ['"yes"', "1", "0"])
def test_booleans_must_be_bool(tmp_path, value):
    path = tmp_path / "config.toml"
    path.write_text(f"[widget]\nshow_free_time = {value}\n")

    with pytest.raises(ConfigError):
        load_config(path)


def test_unknown_keys_and_sections_ignored(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        'unknown_key = "ignored"\n\n'
        "[unknown_section]\nfoo = 1\n\n"
        "[widget]\nshow_free_time = false\n"
    )

    cfg = load_config(path)

    assert cfg.timezone == "Asia/Ho_Chi_Minh"
    assert cfg.widget.show_free_time is False
    assert cfg.widget.max_events == 6
    assert cfg.schedule.day_start == "06:00"


def test_load_config_default_path_uses_config_file(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    config_dir = tmp_path / "cfg" / "hyprschedule"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text('timezone = "UTC"\n')

    cfg = load_config()

    assert cfg.timezone == "UTC"
    assert cfg.schedule.day_start == "06:00"
    assert cfg.widget.max_events == 6


def test_load_config_default_path_missing_file_returns_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))

    cfg = load_config()

    assert cfg.timezone == "Asia/Ho_Chi_Minh"
    assert cfg.widget.max_events == 6