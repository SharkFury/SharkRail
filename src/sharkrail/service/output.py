"""Bounded local-file output storage for asynchronous Jobs."""

from __future__ import annotations

import os
import re
import secrets
import tempfile
import threading
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse

from .windows_security import (
    create_private_temp_file,
    ensure_private_directory,
    secure_private_path,
)

_JOB_DIRECTORY = re.compile(r"^job_[0-9a-f]{32}$")
_STAGING_DIRECTORY = re.compile(r"^\.job_[0-9a-f]{32}\.[0-9a-f]{16}\.tmp$")


class FileOutputStore:
    def __init__(
        self,
        url: str,
        *,
        state_dir: Optional[Path] = None,
        volatile: bool = False,
        max_total_bytes: int = 1024 * 1024 * 1024,
    ) -> None:
        self._temporary = volatile
        self._max_total_bytes = max_total_bytes
        self._lock = threading.RLock()
        if volatile:
            self.root = Path(tempfile.mkdtemp(prefix="sharkrail-output-"))
            secure_private_path(self.root, directory=True)
        else:
            parsed = urlparse(url)
            if parsed.scheme != "file":
                raise ValueError("only file:// output storage is supported")
            raw_path = unquote(parsed.path)
            if url.startswith("file://./"):
                raw_path = url[len("file://") :]
            elif os.name == "nt":  # pragma: no cover - exercised by Windows CI
                raw_path = raw_path.replace("/", "\\")
                if re.fullmatch(r"[A-Za-z]:", parsed.netloc):
                    # Be liberal with the commonly emitted file://C:/... form.
                    raw_path = parsed.netloc + raw_path
                elif parsed.netloc not in {"", "localhost"}:
                    raw_path = f"\\\\{parsed.netloc}{raw_path}"
                elif re.match(r"^\\[A-Za-z]:\\", raw_path):
                    # RFC 8089 local drive URI: file:///C:/path.
                    raw_path = raw_path[1:]
            elif parsed.netloc and parsed.netloc not in {"", "localhost", "."}:
                raw_path = f"//{parsed.netloc}{raw_path}"
            path = Path(raw_path or "./output")
            self.root = path if path.is_absolute() else (state_dir or Path.cwd()) / path
            ensure_private_directory(self.root)

    def write(self, job_id: str, stream: str, data: bytes) -> str:
        if stream not in {"stdout", "stderr"}:
            raise ValueError("unknown output stream")
        if not job_id or Path(job_id).name != job_id:
            raise OSError("invalid output Job ID")
        target_dir = self.root / job_id
        ensure_private_directory(target_dir)
        target = target_dir / f"{stream}.bin"
        with self._lock:
            if self._size_locked() + len(data) > self._max_total_bytes:
                raise OSError("output store capacity exceeded")
            descriptor, temporary_name = create_private_temp_file(
                target_dir, f".{stream}."
            )
            temporary = target_dir / temporary_name
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, target)
                secure_private_path(target, directory=False)
                _sync_directory(target_dir)
            except BaseException:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
                raise
        return str(target)

    def write_job(self, job_id: str, stdout: bytes, stderr: bytes) -> tuple[str, str]:
        """Publish both Job streams with one atomic directory rename."""

        if not job_id or Path(job_id).name != job_id:
            raise OSError("invalid output Job ID")
        target_dir = self.root / job_id
        targets = (target_dir / "stdout.bin", target_dir / "stderr.bin")
        staging_dir = self.root / f".{job_id}.{secrets.token_hex(8)}.tmp"
        with self._lock:
            if os.path.lexists(target_dir):
                ensure_private_directory(target_dir)
            existing_size = sum(
                target.stat().st_size for target in targets if target.is_file()
            )
            if (
                self._size_locked() - existing_size + len(stdout) + len(stderr)
                > self._max_total_bytes
            ):
                raise OSError("output store capacity exceeded")
            ensure_private_directory(staging_dir)
            try:
                for stream, data in (("stdout", stdout), ("stderr", stderr)):
                    descriptor, temporary_name = create_private_temp_file(
                        staging_dir, f".{stream}."
                    )
                    temporary = staging_dir / temporary_name
                    with os.fdopen(descriptor, "wb") as handle:
                        handle.write(data)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temporary, staging_dir / f"{stream}.bin")
                _sync_directory(staging_dir)
                if target_dir.exists():
                    self._remove_job_directory(target_dir)
                os.replace(staging_dir, target_dir)
                _sync_directory(self.root)
            except BaseException:
                self._remove_job_directory(staging_dir, missing_ok=True)
                raise
        return str(targets[0]), str(targets[1])

    def reconcile(self, referenced_job_ids: set[str]) -> int:
        """Remove crash leftovers that are not referenced by the JobStore."""

        removed = 0
        with self._lock:
            for child in tuple(self.root.iterdir()):
                if child.is_symlink() or not child.is_dir():
                    raise OSError(f"unexpected entry in output store: {child.name}")
                is_staging = _STAGING_DIRECTORY.fullmatch(child.name) is not None
                is_job = _JOB_DIRECTORY.fullmatch(child.name) is not None
                if not is_staging and not is_job:
                    raise OSError(f"unexpected entry in output store: {child.name}")
                if not is_staging and child.name in referenced_job_ids:
                    continue
                self._remove_job_directory(child)
                removed += 1
        return removed

    @staticmethod
    def read(path: Optional[str]) -> bytes:
        if path is None:
            return b""
        return Path(path).read_bytes()

    def delete_job(
        self,
        job_id: str,
        stdout_path: Optional[str],
        stderr_path: Optional[str],
    ) -> None:
        """Delete one Job's output without allowing paths to escape the store."""

        if not job_id or Path(job_id).name != job_id:
            raise OSError("invalid output Job ID")
        root = self.root.resolve()
        target_dir = self.root / job_id
        resolved_target = target_dir.resolve()
        if resolved_target.parent != root:
            raise OSError("Job output directory escapes output store")
        expected_names = {"stdout.bin", "stderr.bin"}
        for value in (stdout_path, stderr_path):
            if value is None:
                continue
            supplied = Path(value)
            if (
                supplied.name not in expected_names
                or supplied.parent.resolve() != resolved_target
            ):
                raise OSError("persisted output path escapes Job output directory")

        with self._lock:
            self._remove_job_directory(target_dir, missing_ok=True)

    def close(self) -> None:
        if not self._temporary:
            return
        # Volatile output is intentionally retained until process exit. The OS
        # temporary-directory cleaner handles hard crashes; graceful shutdown
        # removes only files owned by this service instance.
        for path in sorted(self.root.rglob("*"), reverse=True):
            try:
                path.unlink() if path.is_file() else path.rmdir()
            except OSError:
                pass
        try:
            self.root.rmdir()
        except OSError:
            pass

    def _size_locked(self) -> int:
        return sum(
            path.stat().st_size for path in self.root.rglob("*") if path.is_file()
        )

    @staticmethod
    def _remove_job_directory(path: Path, *, missing_ok: bool = False) -> None:
        if not os.path.lexists(path):
            if missing_ok:
                return
            raise FileNotFoundError(path)
        ensure_private_directory(path)
        try:
            children = tuple(path.iterdir())
        except FileNotFoundError:
            if missing_ok:
                return
            raise
        for child in children:
            if not child.is_file() and not child.is_symlink():
                raise OSError("unexpected directory in Job output")
            child.unlink()
        path.rmdir()


def _sync_directory(path: Path) -> None:
    if os.name == "nt":  # os.replace provides the available Python guarantee.
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
