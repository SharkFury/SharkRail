import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sharkrail.service.config import (
    ConfigError,
    example_config_text,
    initialize_config,
    load_config,
    state_directory,
    system_config_path,
)


def _write_config(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    if os.name != "nt":
        path.chmod(0o600)
    return path


def test_missing_implicit_config_uses_volatile_sqlite(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "sharkrail.service.config.system_config_path",
        lambda **_kwargs: tmp_path / "missing.toml",
    )
    config = load_config(environ={})
    assert config.job_store.url == "sqlite:///:memory:"
    assert config.durability == "volatile"
    assert config.config_path is None


def test_explicit_missing_config_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="does not exist"):
        load_config(tmp_path / "missing.toml", require_explicit=True)


def test_strict_config_and_environment_override(tmp_path):
    path = _write_config(
        tmp_path / "service.toml",
        '[server]\nlisten = "127.0.0.1:9000"\n[job_store]\nurl = "sqlite:///jobs.db"\n',
    )
    config = load_config(path, environ={"SHARKRAIL_LISTEN": "127.0.0.1:9001"})
    assert config.server.port == 9001
    assert config.durability == "durable"

    bad = _write_config(tmp_path / "bad.toml", "[server]\nunknown = true\n")
    with pytest.raises(ConfigError, match="unknown configuration setting"):
        load_config(bad)


def test_non_loopback_requires_authentication(tmp_path):
    path = _write_config(
        tmp_path / "service.toml", '[server]\nlisten = "0.0.0.0:8765"\n'
    )
    with pytest.raises(ConfigError, match="auth_token"):
        load_config(path)


def test_store_url_and_url_file_are_mutually_exclusive(tmp_path):
    secret = tmp_path / "database-url"
    secret.write_text("sqlite:///:memory:", encoding="utf-8")
    path = _write_config(
        tmp_path / "service.toml",
        f'[job_store]\nurl = "sqlite:///:memory:"\nurl_file = "{secret}"\n',
    )
    with pytest.raises(ConfigError, match="mutually exclusive"):
        load_config(path)


def test_store_url_file_is_resolved(tmp_path):
    secret = tmp_path / "database-url"
    secret.write_text("sqlite:///jobs.db\n", encoding="utf-8")
    path = _write_config(
        tmp_path / "service.toml",
        f'[job_store]\nurl_file = "{secret}"\n',
    )
    config = load_config(path)
    assert config.job_store.url == "sqlite:///jobs.db"
    assert config.durability == "durable"


def test_invalid_logging_configuration_fails(tmp_path):
    path = _write_config(tmp_path / "service.toml", '[logging]\nformat = "plain"\n')
    with pytest.raises(ConfigError, match="logging.format"):
        load_config(path)


def test_numeric_limits_are_strict_positive_integers(tmp_path):
    path = _write_config(tmp_path / "service.toml", '[executor]\nworkers = "many"\n')
    with pytest.raises(ConfigError, match="positive integer"):
        load_config(path)


def test_system_paths_follow_platform_conventions():
    assert system_config_path(platform="linux", environ={}) == Path(
        "/etc/sharkrail/sharkrail.toml"
    )
    assert (
        system_config_path(platform="win32", environ={"ProgramData": r"D:\SharedData"})
        == Path(r"D:\SharedData") / "SharkRail" / "sharkrail.toml"
    )
    assert (
        state_directory(platform="win32", environ={"ProgramData": r"D:\SharedData"})
        == Path(r"D:\SharedData") / "SharkRail" / "data"
    )


def test_packaged_example_initializes_without_overwrite(tmp_path):
    assert "sqlite:///:memory:" in example_config_text()
    target = tmp_path / "sharkrail.toml"
    assert initialize_config(target) == target
    assert "[job_store]" in target.read_text(encoding="utf-8")
    with pytest.raises(ConfigError, match="refusing to overwrite"):
        initialize_config(target)


def test_config_cli_sample_show_and_init(tmp_path):
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    sample = subprocess.run(
        [sys.executable, "-m", "sharkrail", "config", "sample"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert sample.returncode == 0
    assert "[job_store]" in sample.stdout

    target = tmp_path / "generated.toml"
    initialized = subprocess.run(
        [
            sys.executable,
            "-m",
            "sharkrail",
            "config",
            "init",
            "--path",
            str(target),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert initialized.returncode == 0
    shown = subprocess.run(
        [
            sys.executable,
            "-m",
            "sharkrail",
            "config",
            "show",
            "--config",
            str(target),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    payload = json.loads(shown.stdout)
    assert shown.returncode == 0
    assert payload["durability"] == "volatile"
