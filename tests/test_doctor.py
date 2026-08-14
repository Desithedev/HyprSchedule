from hyprschedule.doctor import CheckResult, exit_code, format_report, run_checks
from hyprschedule.paths import database_path

OPTIONAL_TOOLS = {"notify-send", "pw-play", "eww", "hyprlock", "systemctl", "scheduled"}


def _doctor_env(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


def test_optional_checks_fail_with_empty_path_but_exit_zero(tmp_path, monkeypatch):
    _doctor_env(tmp_path, monkeypatch)
    monkeypatch.setenv("PATH", "")

    results = run_checks()

    assert results
    for result in results:
        if not result.critical:
            assert not result.ok
    assert exit_code(results) == 0


def test_run_checks_contains_critical_and_optional(tmp_path, monkeypatch):
    _doctor_env(tmp_path, monkeypatch)

    results = run_checks()

    names = {r.name for r in results}
    assert names.issuperset(OPTIONAL_TOOLS)
    assert any(r.critical for r in results)
    assert any(not r.critical for r in results)


def test_run_checks_creates_database(tmp_path, monkeypatch):
    _doctor_env(tmp_path, monkeypatch)

    run_checks()

    assert database_path().exists()
    assert database_path().is_file()


def test_exit_code():
    assert exit_code([CheckResult(name="a", ok=True, critical=True)]) == 0
    assert exit_code([CheckResult(name="a", ok=True, critical=False)]) == 0
    assert exit_code([CheckResult(name="a", ok=False, critical=False)]) == 0
    assert exit_code([CheckResult(name="a", ok=False, critical=True)]) == 1


def test_format_report_contains_header_and_markers():
    results = [
        CheckResult(name="python", ok=True, detail="3.14.0"),
        CheckResult(name="database", ok=False, detail="broken", critical=True),
    ]

    report = format_report(results)

    assert "HyprSchedule doctor" in report
    assert "✓" in report
    assert "✗" in report
    assert "python" in report
    assert "database" in report


def test_invalid_config_file_does_not_crash(tmp_path, monkeypatch):
    _doctor_env(tmp_path, monkeypatch)
    config_dir = tmp_path / "config" / "hyprschedule"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text("this is not = = valid toml")

    results = run_checks()

    config_check = next(r for r in results if "config" in r.name.lower())
    assert not config_check.ok