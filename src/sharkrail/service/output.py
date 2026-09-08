"""Bounded local-file output storage for asynchronous Jobs."""

from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse

from .windows_security import secure_private_path


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
        else:
            parsed = urlparse(url)
            if parsed.scheme != "file":
                raise ValueError("only file:// output storage is supported")
            raw_path = unquote(parsed.path)
            if parsed.netloc and parsed.netloc not in {"", "localhost", "."}:
                raw_path = f"//{parsed.netloc}{raw_path}"
            if url.startswith("file://./"):
                raw_path = url[len("file://") :]
            path = Path(raw_path or "./output")
            self.root = path if path.is_absolute() else (state_dir or Path.cwd()) / path
        self.root.mkdir(parents=True, exist_ok=True)
        secure_private_path(self.root, directory=True)

    def write(self, job_id: str, stream: str, data: bytes) -> str:
        if stream not in {"stdout", "stderr"}:
            raise ValueError("unknown output stream")
        target_dir = self.root / job_id
        target_dir.mkdir(parents=True, exist_ok=True)
        secure_private_path(target_dir, directory=True)
        target = target_dir / f"{stream}.bin"
        with self._lock:
            if self._size_locked() + len(data) > self._max_total_bytes:
                raise OSError("output store capacity exceeded")
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{stream}.", dir=str(target_dir)
            )
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    secure_private_path(Path(temporary), directory=False)
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
            try:
                children = tuple(target_dir.iterdir())
            except FileNotFoundError:
                return
            for child in children:
                if not child.is_file() and not child.is_symlink():
                    raise OSError("unexpected directory in Job output")
                child.unlink()
            target_dir.rmdir()

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


def _sync_directory(path: Path) -> None:
    if os.name == "nt":  # os.replace provides the available Python guarantee.
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
