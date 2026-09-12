import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from sharkrail.service.config import (
    CallbackEndpoint,
    ConfigError,
    _getaddrinfo_with_timeout,
    example_config_text,
    initialize_config,
    load_config,
    load_service_execution_policy,
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


def test_darwin_missing_implicit_config_uses_volatile_defaults(monkeypatch):
    expected = system_config_path(platform="darwin", environ={})

    monkeypatch.setattr(
        "sharkrail.service.config.system_config_path", lambda **_kwargs: expected
    )

    def missing(path, *, forbidden_permissions):
        assert path == expected
        assert forbidden_permissions == 0o027
        raise FileNotFoundError(path)

    monkeypatch.setattr(
        "sharkrail.service.config.read_verified_text_file",
        missing,
    )

    config = load_config(environ={})

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


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits only")
def test_config_rejects_world_readable_secrets(tmp_path):
    path = tmp_path / "service.toml"
    path.write_text('[server]\nauth_token = "secret"\n', encoding="utf-8")
    path.chmod(0o644)

    with pytest.raises(ConfigError, match="permissions are too broad"):
        load_config(path)


def test_config_enforces_windows_private_dacl(monkeypatch, tmp_path):
    path = _write_config(tmp_path / "service.toml", "[server]\n")
    denied = PermissionError("Everyone has read access")

    def reject(_path, *, forbidden_permissions):
        assert forbidden_permissions == 0o027
        raise denied

    monkeypatch.setattr(
        "sharkrail.service.config.read_verified_text_file",
        reject,
    )

    with pytest.raises(ConfigError, match="cannot load configuration") as error:
        load_config(path)

    assert error.value.__cause__ is denied


def test_callback_dns_resolution_has_a_hard_deadline(monkeypatch):
    release = threading.Event()

    def blocked_resolution(*_args, **_kwargs):
        release.wait(1)
        return []

    monkeypatch.setattr(
        "sharkrail.service.config.socket.getaddrinfo", blocked_resolution
    )
    try:
        with pytest.raises(TimeoutError, match="exceeded"):
            _getaddrinfo_with_timeout("example.test", 443, timeout=0.01)
    finally:
        release.set()


def test_initialize_config_hardens_windows_acl(tmp_path):
    target = tmp_path / "service.toml"

    with patch("sharkrail.service.config.secure_private_path") as secure:
        initialize_config(target)

    assert any(call.args[0] == target for call in secure.call_args_list)


def test_tenant_tokens_are_validated_and_redacted(tmp_path):
    path = _write_config(
        tmp_path / "service.toml",
        '[server.tenant_tokens]\nalpha = "alpha-secret"\nbeta = "beta-secret"\n',
    )
    config = load_config(path)

    assert config.server.tenant_tokens["alpha"] == "alpha-secret"
    assert config.public_dict()["server"]["tenant_tokens"] == {
        "alpha": "<redacted>",
        "beta": "<redacted>",
    }

    duplicate = _write_config(
        tmp_path / "duplicate.toml",
        '[server.tenant_tokens]\nalpha = "same"\nbeta = "same"\n',
    )
    with pytest.raises(ConfigError, match="unique to one tenant"):
        load_config(duplicate)


def test_authentication_tokens_must_be_distinct_visible_ascii(tmp_path):
    non_ascii = _write_config(
        tmp_path / "non-ascii.toml",
        '[server]\nauth_token = "sëcret"\n',
    )
    with pytest.raises(ConfigError, match="visible ASCII"):
        load_config(non_ascii)

    duplicate_admin = _write_config(
        tmp_path / "duplicate-admin.toml",
        '[server]\nadmin_token = "same"\n[server.tenant_tokens]\ntenant = "same"\n',
    )
    with pytest.raises(ConfigError, match="differ from all tenant tokens"):
        load_config(duplicate_admin)


def test_callback_rejects_non_public_destination_by_default(tmp_path):
    path = _write_config(
        tmp_path / "service.toml",
        '[callback_endpoints.local]\ntenant_id = "default"\n'
        'url = "http://127.0.0.1/hook"\n'
        'secret = "test-secret"\n',
    )

    with pytest.raises(ConfigError, match="non-public address"):
        load_config(path)


def test_callback_private_destination_requires_explicit_opt_in(tmp_path):
    path = _write_config(
        tmp_path / "service.toml",
        '[callback_endpoints.local]\ntenant_id = "default"\n'
        'url = "http://127.0.0.1/hook"\n'
        'secret = "test-secret"\n'
        "allow_private_networks = true\n",
    )

    config = load_config(path)
    assert config.callback_endpoints["local"].allow_private_networks is True


def test_callback_without_authentication_is_rejected():
    endpoint = CallbackEndpoint(
        url="https://example.com/callback",
        tenant_id="tenant",
    )

    with pytest.raises(ConfigError, match="requires secret or secret_file"):
        endpoint.resolved_secret()


def test_empty_callback_secret_file_is_rejected(tmp_path):
    secret = _write_config(tmp_path / "empty-secret", " \n")
    endpoint = CallbackEndpoint(
        url="https://example.com/callback",
        tenant_id="tenant",
        secret_file=str(secret),
    )

    with pytest.raises(ConfigError, match="is empty"):
        endpoint.resolved_secret()


def test_callback_requires_tenant_and_strict_private_network_flag(tmp_path):
    missing_tenant = _write_config(
        tmp_path / "missing-tenant.toml",
        '[callback_endpoints.local]\nurl = "http://127.0.0.1/hook"\n',
    )
    with pytest.raises(ConfigError, match="invalid configuration value"):
        load_config(missing_tenant)

    string_flag = _write_config(
        tmp_path / "string-flag.toml",
        '[callback_endpoints.local]\ntenant_id = "default"\n'
        'url = "http://127.0.0.1/hook"\nallow_private_networks = "true"\n',
    )
    with pytest.raises(ConfigError, match="must be a boolean"):
        load_config(string_flag)


@pytest.mark.parametrize(
    "address",
    [
        "224.0.0.1",
        "[ff02::1]",
        "[fec0::1]",
        "[2002:7f00:1::1]",
        "0.0.0.0",
    ],
)
def test_callback_rejects_non_unicast_destinations(tmp_path, address):
    path = _write_config(
        tmp_path / "non-unicast.toml",
        '[callback_endpoints.unsafe]\ntenant_id = "default"\n'
        f'url = "http://{address}/hook"\n'
        'secret = "test-secret"\n',
    )

    with pytest.raises(ConfigError, match="non-public address"):
        load_config(path)


def test_non_loopback_listener_is_rejected_even_with_authentication(tmp_path):
    path = _write_config(
        tmp_path / "service.toml",
        '[server]\nlisten = "0.0.0.0:8765"\nauth_token = "secret"\n',
    )
    with pytest.raises(ConfigError, match="loopback address"):
        load_config(path)


def test_store_url_and_url_file_are_mutually_exclusive(tmp_path):
    secret = tmp_path / "database-url"
    secret.write_text("sqlite:///:memory:", encoding="utf-8")
    path = _write_config(
        tmp_path / "service.toml",
        "[job_store]\n"
        'url = "sqlite:///:memory:"\n'
        f"url_file = {json.dumps(str(secret))}\n",
    )
    with pytest.raises(ConfigError, match="mutually exclusive"):
        load_config(path)


def test_store_url_file_is_resolved(tmp_path):
    secret = tmp_path / "database-url"
    secret.write_text("sqlite:///jobs.db\n", encoding="utf-8")
    if os.name != "nt":
        secret.chmod(0o600)
    path = _write_config(
        tmp_path / "service.toml",
        f"[job_store]\nurl_file = {json.dumps(str(secret))}\n",
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


def test_service_execution_policy_is_host_owned_and_fail_closed(tmp_path):
    default = load_config(
        _write_config(tmp_path / "default.toml", "[executor]\nworkers = 1\n")
    )
    assert load_service_execution_policy(default).allowed_executables == frozenset()

    policy = tmp_path / "policy.json"
    policy.write_text(
        json.dumps(
            {
                "allowed_executables": ["approved-tool"],
                "allow_parent_environment": False,
                "require_timeout": True,
            }
        ),
        encoding="utf-8",
    )
    if os.name != "nt":
        policy.chmod(0o600)
    configured = load_config(
        _write_config(
            tmp_path / "configured.toml",
            f"[executor]\npolicy_file = {json.dumps(str(policy))}\n",
        )
    )
    loaded = load_service_execution_policy(configured)
    assert loaded.allowed_executables == frozenset({"approved-tool"})
    assert loaded.allow_parent_environment is False


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits only")
def test_service_execution_policy_rejects_group_writable_file(tmp_path):
    policy = tmp_path / "policy.json"
    policy.write_text('{"allowed_executables": []}', encoding="utf-8")
    policy.chmod(0o666)
    config = _write_config(
        tmp_path / "service.toml",
        f"[executor]\npolicy_file = {json.dumps(str(policy))}\n",
    )

    with pytest.raises(ConfigError, match="permissions are too broad"):
        load_config(config)


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_sensitive_file_reads_reject_symlinked_parent(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    config = _write_config(real / "service.toml", "[server]\n")
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)

    with pytest.raises(ConfigError, match="cannot load configuration"):
        load_config(linked / config.name)


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink and FIFO semantics")
def test_sensitive_file_reads_reject_final_symlinks_and_fifos(tmp_path):
    real_config = _write_config(tmp_path / "real.toml", "[server]\n")
    linked_config = tmp_path / "linked.toml"
    linked_config.symlink_to(real_config)
    with pytest.raises(ConfigError, match="cannot load configuration"):
        load_config(linked_config)

    fifo_config = tmp_path / "fifo.toml"
    os.mkfifo(fifo_config, 0o600)
    with pytest.raises(ConfigError, match="regular file"):
        load_config(fifo_config)


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_policy_and_callback_secret_reject_symlinks(tmp_path):
    policy = _write_config(tmp_path / "policy.json", "{}")
    policy_link = tmp_path / "policy-link.json"
    policy_link.symlink_to(policy)
    configured = _write_config(
        tmp_path / "service.toml",
        f"[executor]\npolicy_file = {json.dumps(str(policy_link))}\n",
    )
    with pytest.raises(ConfigError, match="cannot load executor policy_file"):
        load_config(configured)

    secret = _write_config(tmp_path / "secret", "secret")
    secret_link = tmp_path / "secret-link"
    secret_link.symlink_to(secret)
    endpoint = CallbackEndpoint(
        url="https://example.com/callback",
        tenant_id="tenant",
        secret_file=str(secret_link),
    )
    with pytest.raises(ConfigError, match="cannot read callback secret"):
        endpoint.resolved_secret()


def test_system_paths_follow_platform_conventions():
    assert system_config_path(platform="linux", environ={}) == Path(
        "/etc/sharkrail/sharkrail.toml"
    )
    assert (
        system_config_path(platform="win32", environ={"ProgramData": r"D:\SharedData"})
        == Path(r"D:\SharedData") / "SharkRail" / "sharkrail.toml"
    )
    assert system_config_path(platform="darwin", environ={}) == Path(
        "/private/etc/sharkrail/sharkrail.toml"
    )
    assert state_directory(platform="darwin", environ={}) == Path(
        "/private/var/lib/sharkrail"
    )
    assert (
        state_directory(platform="win32", environ={"ProgramData": r"D:\SharedData"})
        == Path(r"D:\SharedData") / "SharkRail" / "data"
    )


def test_documented_macos_paths_match_runtime_defaults():
    documentation = (
        Path(__file__).resolve().parents[1] / "docs" / "CONFIGURATION.md"
    ).read_text(encoding="utf-8")

    assert str(system_config_path(platform="darwin", environ={})) in documentation
    assert str(state_directory(platform="darwin", environ={})) in documentation
    assert "`sharkrail config paths`" in documentation


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
