"""Strict, platform-aware service configuration."""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from importlib import resources
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

if sys.version_info >= (3, 11):  # pragma: no cover - selected by interpreter
    import tomllib
else:  # pragma: no cover - exercised on supported Python 3.9/3.10
    import tomli as tomllib


class ConfigError(ValueError):
    """Raised when service configuration is invalid."""


@dataclass(frozen=True)
class ServerSettings:
    listen: str = "127.0.0.1:8765"
    request_body_max_bytes: int = 1024 * 1024
    request_timeout_seconds: int = 30
    auth_token: Optional[str] = None

    @property
    def host(self) -> str:
        return self.listen.rsplit(":", 1)[0]

    @property
    def port(self) -> int:
        return int(self.listen.rsplit(":", 1)[1])


@dataclass(frozen=True)
class JobStoreSettings:
    url: str = "sqlite:///:memory:"
    url_file: Optional[str] = None


@dataclass(frozen=True)
class OutputStoreSettings:
    url: str = "file://./output"
    max_total_bytes: int = 1024 * 1024 * 1024


@dataclass(frozen=True)
class ControlSettings:
    api_workers: int = 2
    controller_workers: int = 1
    notification_workers: int = 1
    worker_heartbeat_seconds: int = 5
    worker_progress_timeout_seconds: int = 30
    worker_drain_timeout_seconds: int = 20
    worker_restart_limit: int = 5
    worker_restart_window_seconds: int = 60


@dataclass(frozen=True)
class ExecutorSettings:
    workers: int = 2
    heartbeat_seconds: int = 5
    lease_seconds: int = 30


@dataclass(frozen=True)
class AdmissionSettings:
    max_concurrent_requests: int = 128
    max_queued_jobs: int = 1000
    max_queued_jobs_per_tenant: int = 100


@dataclass(frozen=True)
class VolatileStoreSettings:
    max_jobs: int = 1000
    max_metadata_bytes: int = 64 * 1024 * 1024
    max_event_records: int = 10000
    job_ttl_seconds: int = 3600
    max_temporary_output_bytes: int = 1024 * 1024 * 1024


@dataclass(frozen=True)
class NotificationSettings:
    max_concurrent_deliveries: int = 16
    request_timeout_seconds: int = 10
    max_attempts: int = 12


@dataclass(frozen=True)
class LoggingSettings:
    level: str = "INFO"
    format: str = "json"


@dataclass(frozen=True)
class CallbackEndpoint:
    url: str
    secret: Optional[str] = None
    secret_file: Optional[str] = None

    def resolved_secret(self) -> Optional[str]:
        if self.secret is not None:
            return self.secret
        if self.secret_file is None:
            return None
        try:
            return Path(self.secret_file).read_text(encoding="utf-8").strip()
        except OSError as err:
            raise ConfigError(
                f"cannot read callback secret file {self.secret_file!r}: {err}"
            ) from err


@dataclass(frozen=True)
class ServiceConfig:
    server: ServerSettings = field(default_factory=ServerSettings)
    job_store: JobStoreSettings = field(default_factory=JobStoreSettings)
    output_store: OutputStoreSettings = field(default_factory=OutputStoreSettings)
    control: ControlSettings = field(default_factory=ControlSettings)
    executor: ExecutorSettings = field(default_factory=ExecutorSettings)
    admission: AdmissionSettings = field(default_factory=AdmissionSettings)
    volatile_store: VolatileStoreSettings = field(default_factory=VolatileStoreSettings)
    notifications: NotificationSettings = field(default_factory=NotificationSettings)
    logging: LoggingSettings = field(default_factory=LoggingSettings)
    callback_endpoints: Mapping[str, CallbackEndpoint] = field(default_factory=dict)
    config_path: Optional[Path] = None

    @property
    def durability(self) -> str:
        return "volatile" if self.job_store.url == "sqlite:///:memory:" else "durable"

    def public_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["config_path"] = str(self.config_path) if self.config_path else None
        job_store = result["job_store"]
        if isinstance(job_store, dict) and "url" in job_store:
            job_store["url"] = _redact_url(str(job_store["url"]))
        server = result["server"]
        if isinstance(server, dict) and server.get("auth_token"):
            server["auth_token"] = "<redacted>"
        endpoints = result["callback_endpoints"]
        if isinstance(endpoints, dict):
            for endpoint in endpoints.values():
                if isinstance(endpoint, dict) and endpoint.get("secret"):
                    endpoint["secret"] = "<redacted>"
        result["durability"] = self.durability
        return result


_SECTIONS: dict[str, set[str]] = {
    "server": {
        "listen",
        "request_body_max_bytes",
        "request_timeout_seconds",
        "auth_token",
    },
    "job_store": {"url", "url_file"},
    "output_store": {"url", "max_total_bytes"},
    "control": {
        "api_workers",
        "controller_workers",
        "notification_workers",
        "worker_heartbeat_seconds",
        "worker_progress_timeout_seconds",
        "worker_drain_timeout_seconds",
        "worker_restart_limit",
        "worker_restart_window_seconds",
    },
    "executor": {"workers", "heartbeat_seconds", "lease_seconds"},
    "admission": {
        "max_concurrent_requests",
        "max_queued_jobs",
        "max_queued_jobs_per_tenant",
    },
    "volatile_store": {
        "max_jobs",
        "max_metadata_bytes",
        "max_event_records",
        "job_ttl_seconds",
        "max_temporary_output_bytes",
    },
    "notifications": {
        "max_concurrent_deliveries",
        "request_timeout_seconds",
        "max_attempts",
    },
    "logging": {"level", "format"},
    "callback_endpoints": set(),
}

_ENV_KEYS = {
    "SHARKRAIL_JOB_STORE_URL": ("job_store", "url"),
    "SHARKRAIL_JOB_STORE_URL_FILE": ("job_store", "url_file"),
    "SHARKRAIL_OUTPUT_STORE_URL": ("output_store", "url"),
    "SHARKRAIL_LISTEN": ("server", "listen"),
    "SHARKRAIL_AUTH_TOKEN": ("server", "auth_token"),
}


def system_config_path(
    *, platform: Optional[str] = None, environ: Optional[Mapping[str, str]] = None
) -> Path:
    """Return the system service configuration path for the current platform."""

    platform = sys.platform if platform is None else platform
    environ = os.environ if environ is None else environ
    if platform == "win32":
        base = environ.get("ProgramData") or environ.get("PROGRAMDATA")
        if not base:
            base = _windows_program_data()
        return Path(base) / "SharkRail" / "sharkrail.toml"
    return Path("/etc/sharkrail/sharkrail.toml")


def state_directory(
    *, platform: Optional[str] = None, environ: Optional[Mapping[str, str]] = None
) -> Path:
    """Return the persistent service data directory."""

    platform = sys.platform if platform is None else platform
    environ = os.environ if environ is None else environ
    override = environ.get("SHARKRAIL_STATE_DIR")
    if override:
        return Path(override)
    if platform == "win32":
        base = environ.get("ProgramData") or environ.get("PROGRAMDATA")
        if not base:
            base = _windows_program_data()
        return Path(base) / "SharkRail" / "data"
    return Path("/var/lib/sharkrail")


def load_config(
    path: Optional[Path] = None,
    *,
    environ: Optional[Mapping[str, str]] = None,
    require_explicit: bool = False,
) -> ServiceConfig:
    """Load a strict config file and environment overrides."""

    env = os.environ if environ is None else environ
    explicit_env = env.get("SHARKRAIL_CONFIG_FILE")
    selected = path or (
        Path(explicit_env) if explicit_env else system_config_path(environ=env)
    )
    explicit = path is not None or explicit_env is not None or require_explicit
    raw: dict[str, Any] = {}
    selected_path: Optional[Path] = None
    if selected.exists():
        _validate_permissions(selected)
        try:
            with selected.open("rb") as handle:
                loaded = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as err:
            raise ConfigError(f"cannot load configuration {selected}: {err}") from err
        if not isinstance(loaded, dict):
            raise ConfigError("configuration root must be a table")
        raw = loaded
        selected_path = selected
    elif explicit:
        raise ConfigError(f"configuration file does not exist: {selected}")

    _validate_keys(raw)
    merged = _apply_environment(raw, env)
    try:
        config = _build_config(merged, selected_path)
    except TypeError as err:
        raise ConfigError(f"invalid configuration value: {err}") from err
    try:
        return _validate_config(config, merged)
    except ConfigError:
        raise
    except (AttributeError, TypeError, ValueError) as err:
        raise ConfigError(f"invalid configuration value: {err}") from err


def example_config_text() -> str:
    """Return the configuration example embedded in the installed package."""

    return (
        resources.files("sharkrail.resources")
        .joinpath("sharkrail.toml.example")
        .read_text(encoding="utf-8")
    )


def initialize_config(path: Path, *, force: bool = False) -> Path:
    """Atomically install the embedded example as an active configuration."""

    if path.exists() and not force:
        raise ConfigError(f"refusing to overwrite existing configuration: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".sharkrail.", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(example_config_text())
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            os.chmod(temporary, 0o640)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return path


def _build_config(raw: Mapping[str, Any], path: Optional[Path]) -> ServiceConfig:
    endpoints_raw = raw.get("callback_endpoints", {})
    endpoints = {
        name: CallbackEndpoint(**values) for name, values in endpoints_raw.items()
    }
    return ServiceConfig(
        server=ServerSettings(**raw.get("server", {})),
        job_store=JobStoreSettings(**raw.get("job_store", {})),
        output_store=OutputStoreSettings(**raw.get("output_store", {})),
        control=ControlSettings(**raw.get("control", {})),
        executor=ExecutorSettings(**raw.get("executor", {})),
        admission=AdmissionSettings(**raw.get("admission", {})),
        volatile_store=VolatileStoreSettings(**raw.get("volatile_store", {})),
        notifications=NotificationSettings(**raw.get("notifications", {})),
        logging=LoggingSettings(**raw.get("logging", {})),
        callback_endpoints=endpoints,
        config_path=path,
    )


def _validate_keys(raw: Mapping[str, Any]) -> None:
    unknown_sections = set(raw) - set(_SECTIONS)
    if unknown_sections:
        raise ConfigError(f"unknown configuration section: {min(unknown_sections)}")
    for section, allowed in _SECTIONS.items():
        values = raw.get(section, {})
        if not isinstance(values, dict):
            raise ConfigError(f"configuration section {section!r} must be a table")
        if section == "callback_endpoints":
            for name, endpoint in values.items():
                if not isinstance(endpoint, dict):
                    raise ConfigError(f"callback endpoint {name!r} must be a table")
                unknown = set(endpoint) - {"url", "secret", "secret_file"}
                if unknown:
                    raise ConfigError(
                        f"unknown callback endpoint setting: {name}.{min(unknown)}"
                    )
            continue
        unknown = set(values) - allowed
        if unknown:
            raise ConfigError(
                f"unknown configuration setting: {section}.{min(unknown)}"
            )


def _apply_environment(
    raw: Mapping[str, Any], env: Mapping[str, str]
) -> dict[str, Any]:
    merged = {key: dict(value) for key, value in raw.items()}
    for env_name, (section, key) in _ENV_KEYS.items():
        if env_name in env:
            merged.setdefault(section, {})[key] = env[env_name]
    return merged


def _validate_config(
    config: ServiceConfig, raw: Optional[Mapping[str, Any]] = None
) -> ServiceConfig:
    try:
        host, port = config.server.listen.rsplit(":", 1)
        port_value = int(port)
    except (ValueError, AttributeError) as err:
        raise ConfigError("server.listen must be HOST:PORT") from err
    if not host or not 0 <= port_value <= 65535:
        raise ConfigError("server.listen must contain a valid host and port")
    if config.server.auth_token is not None and (
        not isinstance(config.server.auth_token, str) or not config.server.auth_token
    ):
        raise ConfigError("server.auth_token must be a non-empty string")
    if host not in {"127.0.0.1", "localhost", "::1"} and not config.server.auth_token:
        raise ConfigError("server.auth_token is required for a non-loopback listener")
    _positive_values(config)

    job_store_raw = (raw or {}).get("job_store", {})
    direct_url_configured = isinstance(job_store_raw, dict) and "url" in job_store_raw
    if config.job_store.url_file and direct_url_configured:
        raise ConfigError("job_store.url and job_store.url_file are mutually exclusive")
    store_url = config.job_store.url
    if config.job_store.url_file:
        try:
            store_url = (
                Path(config.job_store.url_file).read_text(encoding="utf-8").strip()
            )
        except OSError as err:
            raise ConfigError(f"cannot read job_store.url_file: {err}") from err
        if not store_url:
            raise ConfigError("job_store.url_file is empty")
        config = replace(config, job_store=replace(config.job_store, url=store_url))
    if not store_url.startswith("sqlite:///"):
        raise ConfigError("only sqlite:/// JobStore URLs are currently supported")
    output = urlparse(config.output_store.url)
    if output.scheme != "file":
        raise ConfigError("only file:// OutputStore URLs are currently supported")
    for name, endpoint in config.callback_endpoints.items():
        parsed = urlparse(endpoint.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ConfigError(f"callback endpoint {name!r} must use an HTTP(S) URL")
        if endpoint.secret and endpoint.secret_file:
            raise ConfigError(
                f"callback endpoint {name!r} cannot set secret and secret_file"
            )
        endpoint.resolved_secret()
    if config.logging.level.upper() not in {
        "CRITICAL",
        "ERROR",
        "WARNING",
        "INFO",
        "DEBUG",
    }:
        raise ConfigError("logging.level must be a standard Python logging level")
    if config.logging.format != "json":
        raise ConfigError("only logging.format=json is currently supported")
    return config


def _positive_values(config: ServiceConfig) -> None:
    groups = (
        config.server,
        config.output_store,
        config.control,
        config.executor,
        config.admission,
        config.volatile_store,
        config.notifications,
    )
    for group in groups:
        for name, value in asdict(group).items():
            default_value = getattr(group.__class__(), name)
            if isinstance(default_value, int) and (
                not isinstance(value, int) or isinstance(value, bool) or value <= 0
            ):
                raise ConfigError(
                    f"{group.__class__.__name__}.{name} must be a positive integer"
                )


def _validate_permissions(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        mode = path.stat().st_mode
    except OSError as err:
        raise ConfigError(f"cannot inspect configuration permissions: {err}") from err
    if mode & 0o022:
        raise ConfigError(
            f"configuration is group/world writable: {path}; expected mode 0640 or stricter"
        )


def _windows_program_data() -> str:
    try:  # pragma: no cover - Windows-only known-folder lookup
        import ctypes
        from ctypes import wintypes
        from uuid import UUID

        guid_bytes = UUID("62ab5d82-fdc1-4dc3-a9dd-070d1d495d97").bytes_le

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        guid = GUID.from_buffer_copy(guid_bytes)
        path_ptr = ctypes.c_wchar_p()
        windll = ctypes.windll  # type: ignore[attr-defined]
        result = windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(guid), 0, None, ctypes.byref(path_ptr)
        )
        if result != 0:
            raise OSError(result, "SHGetKnownFolderPath failed")
        try:
            value = path_ptr.value
            if value is None:
                raise OSError("SHGetKnownFolderPath returned an empty path")
            return value
        finally:
            windll.ole32.CoTaskMemFree(path_ptr)
    except Exception as err:
        raise ConfigError("cannot resolve Windows ProgramData directory") from err


def _redact_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.password is None:
        return value
    username = parsed.username or ""
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{username}:<redacted>@{host}{port}{parsed.path}"
