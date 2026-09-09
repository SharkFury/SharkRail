"""Windows DACL helpers for sensitive service files and directories."""

from __future__ import annotations

import os
import secrets
import stat
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

_UNSAFE_SHARED_DIRECTORIES = {
    Path("/"),
    Path("/tmp"),
    Path("/var/tmp"),
    Path("/dev/shm"),
}


def ensure_private_directory(path: Path) -> bool:
    """Create a service-owned directory, or validate an existing one.

    Existing directories are never chmod'ed on POSIX.  This is important for
    configured storage URLs: a typo such as ``file:///`` must not mutate a
    shared or system directory.
    """

    path = Path(os.path.abspath(path))
    if os.name != "nt" and _is_unsafe_shared_directory(path):
        raise PermissionError(f"refusing to use shared system directory: {path}")
    if os.path.lexists(path):
        _validate_posix_private_path(path, directory=True)
        if os.name == "nt":  # pragma: no cover - exercised by Windows CI
            if path.is_symlink() or not path.is_dir():
                raise PermissionError(f"private path is not a real directory: {path}")
            validate_private_path(path)
        return False
    missing: list[Path] = []
    candidate = path
    while not os.path.lexists(candidate):
        missing.append(candidate)
        if candidate.parent == candidate:
            break
        candidate = candidate.parent
    for candidate in reversed(missing):
        try:
            candidate.mkdir(mode=0o700)
        except FileExistsError:
            _validate_posix_private_path(candidate, directory=True)
            if os.name == "nt":  # pragma: no cover - exercised by Windows CI
                if candidate.is_symlink() or not candidate.is_dir():
                    raise PermissionError(
                        f"private path is not a real directory: {candidate}"
                    )
                validate_private_path(candidate)
        else:
            secure_private_path(candidate, directory=True)
    return True


def open_private_file(path: Path, flags: int = os.O_RDWR) -> int:
    """Open/create one private regular file without following its final name."""

    path = Path(path)
    if os.name == "nt":  # pragma: no cover - exercised by Windows CI
        created = not path.exists()
        descriptor = os.open(path, flags | os.O_CREAT, 0o600)
        try:
            if created:
                secure_private_path(path, directory=False)
            else:
                validate_private_path(path)
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    directory = os.open(path.parent, directory_flags)
    try:
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(
                path.name,
                flags | os.O_CREAT | os.O_EXCL | nofollow,
                0o600,
                dir_fd=directory,
            )
            created = True
        except FileExistsError:
            descriptor = os.open(
                path.name,
                flags | nofollow | getattr(os, "O_NONBLOCK", 0),
                dir_fd=directory,
            )
            created = False
        try:
            if os.name != "nt":
                if created:
                    os.fchmod(descriptor, 0o600)
                _validate_private_file_descriptor(descriptor, path)
            elif created:  # pragma: no cover - exercised by Windows CI
                secure_private_path(path, directory=False)
            else:  # pragma: no cover - exercised by Windows CI
                validate_private_path(path)
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor
    finally:
        os.close(directory)


def create_private_temp_file(directory: Path, prefix: str) -> tuple[int, str]:
    """Create a private temporary file through a verified directory handle."""

    if os.name == "nt":  # pragma: no cover - exercised by Windows CI
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        for _ in range(128):
            name = f"{prefix}{secrets.token_hex(8)}"
            path = directory / name
            try:
                descriptor = os.open(path, flags, 0o600)
            except FileExistsError:
                continue
            try:
                secure_private_path(path, directory=False)
            except BaseException:
                os.close(descriptor)
                try:
                    path.unlink()
                except OSError:
                    pass
                raise
            return descriptor, name
        raise FileExistsError("could not allocate a unique private temporary file")

    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(directory, directory_flags)
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        for _ in range(128):
            name = f"{prefix}{secrets.token_hex(8)}"
            try:
                descriptor = os.open(name, flags, 0o600, dir_fd=directory_fd)
            except FileExistsError:
                continue
            if os.name != "nt":
                os.fchmod(descriptor, 0o600)
            return descriptor, name
    finally:
        os.close(directory_fd)
    raise FileExistsError("could not allocate a unique private temporary file")


def secure_private_path(path: Path, *, directory: bool | None = None) -> None:
    """Restrict a sensitive path to the service identity on every platform."""

    if os.name != "nt":
        is_directory = path.is_dir() if directory is None else directory
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        if is_directory:
            flags |= getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(path, flags)
        try:
            os.fchmod(descriptor, 0o700 if is_directory else 0o600)
        finally:
            os.close(descriptor)
        return
    is_directory = path.is_dir() if directory is None else directory
    _set_private_windows_acl(path, directory=is_directory)


def validate_private_path(path: Path) -> None:
    """Reject a Windows path whose owner or allow ACEs cross the trust boundary."""

    if os.name != "nt":
        _validate_posix_private_path(path, directory=None)
        return
    _validate_private_windows_acl(path)


def validate_private_file(path: Path) -> None:
    """Require a service-owned regular file with no access for other users."""

    if os.name != "nt":
        _validate_posix_private_path(path, directory=False)
        return
    metadata = os.lstat(path)
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if attributes & reparse or path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise PermissionError(f"private path is not a regular file: {path}")
    _validate_private_windows_acl(path)


def read_verified_text_file(path: Path, *, forbidden_permissions: int) -> str:
    """Read a sensitive regular file through the descriptor that was validated.

    POSIX components are opened relative to non-following directory descriptors.
    Windows rejects reparse points in every component and validates the DACL on
    the same final file handle used for the read.
    """

    absolute = Path(os.path.abspath(path))
    if os.name == "nt":  # pragma: no cover - exercised by Windows CI
        descriptor = _open_verified_windows_file(absolute)
    else:
        descriptor = _open_verified_posix_file(absolute)
    try:
        _validate_sensitive_file_descriptor(
            descriptor,
            absolute,
            forbidden_permissions=forbidden_permissions,
        )
        if os.name == "nt":  # pragma: no cover - exercised by Windows CI
            _validate_private_windows_handle(descriptor, absolute)
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            descriptor = -1
            return handle.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _open_verified_posix_file(path: Path) -> int:
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    directory = os.open(path.anchor or os.sep, directory_flags)
    try:
        for component in path.parts[1:-1]:
            child = os.open(component, directory_flags, dir_fd=directory)
            os.close(directory)
            directory = child
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        return os.open(path.name, flags, dir_fd=directory)
    finally:
        os.close(directory)


def _is_unsafe_shared_directory(path: Path) -> bool:
    candidates = set(_UNSAFE_SHARED_DIRECTORIES)
    candidates.add(Path(os.path.abspath(tempfile.gettempdir())))
    for candidate in tuple(candidates):
        try:
            candidates.add(candidate.resolve())
        except OSError:
            continue
    return path in candidates


def _validate_posix_private_path(path: Path, *, directory: Optional[bool]) -> None:
    if os.name == "nt":
        return
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        raise
    except OSError as err:
        raise PermissionError(f"cannot inspect private path {path}: {err}") from err
    if stat.S_ISLNK(metadata.st_mode):
        raise PermissionError(f"private path must not be a symbolic link: {path}")
    if directory is True and not stat.S_ISDIR(metadata.st_mode):
        raise PermissionError(f"private path is not a directory: {path}")
    if directory is False and not stat.S_ISREG(metadata.st_mode):
        raise PermissionError(f"private path is not a regular file: {path}")
    if metadata.st_uid != os.geteuid():
        raise PermissionError(f"private path is not owned by the service user: {path}")
    forbidden = 0o022 if directory is not False else 0o077
    if stat.S_IMODE(metadata.st_mode) & forbidden:
        raise PermissionError(f"private path permissions are too broad: {path}")


def _validate_private_file_descriptor(descriptor: int, path: Path) -> None:
    _validate_sensitive_file_descriptor(
        descriptor,
        path,
        forbidden_permissions=0o077,
    )


def _validate_sensitive_file_descriptor(
    descriptor: int,
    path: Path,
    *,
    forbidden_permissions: int,
) -> None:
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode):
        raise PermissionError(f"private path is not a regular file: {path}")
    if os.name != "nt" and metadata.st_uid != os.geteuid():
        raise PermissionError(f"private path is not owned by the service user: {path}")
    if os.name != "nt" and stat.S_IMODE(metadata.st_mode) & forbidden_permissions:
        raise PermissionError(f"private path permissions are too broad: {path}")


if os.name == "nt":  # pragma: no cover - exercised by the Windows CI matrix
    import ctypes
    from ctypes import wintypes

    _SDDL_REVISION_1 = 1
    _SE_FILE_OBJECT = 1
    _OWNER_SECURITY_INFORMATION = 0x00000001
    _DACL_SECURITY_INFORMATION = 0x00000004
    _PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
    _ACL_SIZE_INFORMATION_CLASS = 2
    _GENERIC_READ = 0x80000000
    _FILE_SHARE_READ = 0x00000001
    _OPEN_EXISTING = 3
    _FILE_ATTRIBUTE_DIRECTORY = 0x00000010
    _FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    _INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
    _TOKEN_QUERY = 0x0008
    _TOKEN_USER_CLASS = 1
    _ERROR_INSUFFICIENT_BUFFER = 122
    _INHERIT_ONLY_ACE = 0x08
    _ACE_OBJECT_TYPE_PRESENT = 0x1
    _ACE_INHERITED_OBJECT_TYPE_PRESENT = 0x2

    # ACCESS_ALLOWED[_OBJECT][_CALLBACK] ACEs. Conditional data, when present,
    # follows the SID and therefore does not change the SID offset.
    _ALLOW_ACE_TYPES = {0, 5, 9, 11}
    _NON_GRANT_ACE_TYPES = {1, 2, 3, 6, 7, 8, 10, 12, 13, 15, 17, 18, 19, 20, 21}
    _OBJECT_ACE_TYPES = {5, 6, 7, 8, 11, 12, 13, 15}
    _ALWAYS_TRUSTED_SIDS = {
        "S-1-5-18",  # LocalSystem
        "S-1-5-32-544",  # Builtin Administrators
        "S-1-3-4",  # Owner Rights
    }

    class _SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]

    class _TOKEN_USER(ctypes.Structure):
        _fields_ = [("User", _SID_AND_ATTRIBUTES)]

    class _ACE_HEADER(ctypes.Structure):
        _fields_ = [
            ("AceType", ctypes.c_ubyte),
            ("AceFlags", ctypes.c_ubyte),
            ("AceSize", ctypes.c_ushort),
        ]

    class _ACL_SIZE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("AceCount", wintypes.DWORD),
            ("AclBytesInUse", wintypes.DWORD),
            ("AclBytesFree", wintypes.DWORD),
        ]

    class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]

    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
    _kernel32.LocalFree.restype = ctypes.c_void_p
    _kernel32.GetFileAttributesW.argtypes = (wintypes.LPCWSTR,)
    _kernel32.GetFileAttributesW.restype = wintypes.DWORD
    _kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.GetFileInformationByHandle.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION),
    )
    _kernel32.GetFileInformationByHandle.restype = wintypes.BOOL

    _advapi32.OpenProcessToken.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    )
    _advapi32.OpenProcessToken.restype = wintypes.BOOL
    _advapi32.GetTokenInformation.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    _advapi32.GetTokenInformation.restype = wintypes.BOOL
    _advapi32.ConvertSidToStringSidW.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.LPWSTR),
    )
    _advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    )
    _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = (
        wintypes.BOOL
    )
    _advapi32.GetSecurityDescriptorDacl.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.BOOL),
    )
    _advapi32.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    _advapi32.SetNamedSecurityInfoW.argtypes = (
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    )
    _advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD
    _advapi32.GetNamedSecurityInfoW.argtypes = (
        wintypes.LPWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    )
    _advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    _advapi32.GetSecurityInfo.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    )
    _advapi32.GetSecurityInfo.restype = wintypes.DWORD
    _advapi32.GetAclInformation.argtypes = (
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_int,
    )
    _advapi32.GetAclInformation.restype = wintypes.BOOL
    _advapi32.GetAce.argtypes = (
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    )
    _advapi32.GetAce.restype = wintypes.BOOL

    def _last_error(operation: str) -> OSError:
        error = ctypes.get_last_error()  # type: ignore[attr-defined]
        detail = ctypes.FormatError(error)  # type: ignore[attr-defined]
        return OSError(error, f"{operation} failed: {detail}")

    def _result_error(operation: str, error: int) -> OSError:
        detail = ctypes.FormatError(error)  # type: ignore[attr-defined]
        return OSError(error, f"{operation} failed: {detail}")

    def _open_verified_windows_file(path: Path) -> int:
        import msvcrt

        windows_msvcrt: Any = msvcrt
        current = Path(path.anchor)
        for component in path.parts[1:-1]:
            current /= component
            attributes = _kernel32.GetFileAttributesW(str(current))
            if attributes == _INVALID_FILE_ATTRIBUTES:
                raise _last_error("GetFileAttributesW")
            if not attributes & _FILE_ATTRIBUTE_DIRECTORY:
                raise PermissionError(
                    f"sensitive path component is not a directory: {current}"
                )
            if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                raise PermissionError(
                    f"sensitive path contains a reparse point: {current}"
                )
        handle = _kernel32.CreateFileW(
            str(path),
            _GENERIC_READ,
            _FILE_SHARE_READ,
            None,
            _OPEN_EXISTING,
            _FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        invalid = ctypes.c_void_p(-1).value
        if handle == invalid:
            raise _last_error("CreateFileW")
        try:
            information = _BY_HANDLE_FILE_INFORMATION()
            if not _kernel32.GetFileInformationByHandle(
                handle, ctypes.byref(information)
            ):
                raise _last_error("GetFileInformationByHandle")
            if information.dwFileAttributes & (
                _FILE_ATTRIBUTE_DIRECTORY | _FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise PermissionError(
                    f"sensitive path is not a non-reparse regular file: {path}"
                )
            return windows_msvcrt.open_osfhandle(
                int(handle), os.O_RDONLY | getattr(os, "O_BINARY", 0)
            )
        except BaseException:
            _kernel32.CloseHandle(handle)
            raise

    def _sid_string(sid: ctypes.c_void_p) -> str:
        value = wintypes.LPWSTR()
        if not _advapi32.ConvertSidToStringSidW(sid, ctypes.byref(value)):
            raise _last_error("ConvertSidToStringSidW")
        try:
            result = value.value
            if result is None:
                raise OSError("ConvertSidToStringSidW returned an empty SID")
            return result
        finally:
            _kernel32.LocalFree(ctypes.cast(value, ctypes.c_void_p))

    @lru_cache(maxsize=1)
    def _current_user_sid() -> str:
        token = wintypes.HANDLE()
        if not _advapi32.OpenProcessToken(
            _kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)
        ):
            raise _last_error("OpenProcessToken")
        try:
            size = wintypes.DWORD()
            _advapi32.GetTokenInformation(
                token, _TOKEN_USER_CLASS, None, 0, ctypes.byref(size)
            )
            if ctypes.get_last_error() != _ERROR_INSUFFICIENT_BUFFER:  # type: ignore[attr-defined]
                raise _last_error("GetTokenInformation")
            buffer = ctypes.create_string_buffer(size.value)
            if not _advapi32.GetTokenInformation(
                token,
                _TOKEN_USER_CLASS,
                buffer,
                size,
                ctypes.byref(size),
            ):
                raise _last_error("GetTokenInformation")
            user = ctypes.cast(buffer, ctypes.POINTER(_TOKEN_USER)).contents
            return _sid_string(user.User.Sid)
        finally:
            _kernel32.CloseHandle(token)

    def _set_private_windows_acl(path: Path, *, directory: bool) -> None:
        sid = _current_user_sid()
        inheritance = "OICI" if directory else ""
        sddl = (
            "D:P"
            f"(A;{inheritance};FA;;;SY)"
            f"(A;{inheritance};FA;;;BA)"
            f"(A;{inheritance};FA;;;{sid})"
        )
        _set_windows_dacl(path, sddl)

    def _set_windows_dacl(path: Path, sddl: str) -> None:
        """Apply a protected DACL expressed as SDDL to one filesystem path."""

        descriptor = ctypes.c_void_p()
        descriptor_size = wintypes.DWORD()
        if not _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl,
            _SDDL_REVISION_1,
            ctypes.byref(descriptor),
            ctypes.byref(descriptor_size),
        ):
            raise _last_error("ConvertStringSecurityDescriptorToSecurityDescriptorW")
        try:
            present = wintypes.BOOL()
            defaulted = wintypes.BOOL()
            dacl = ctypes.c_void_p()
            if not _advapi32.GetSecurityDescriptorDacl(
                descriptor,
                ctypes.byref(present),
                ctypes.byref(dacl),
                ctypes.byref(defaulted),
            ):
                raise _last_error("GetSecurityDescriptorDacl")
            if not present.value or not dacl.value:
                raise OSError("generated private security descriptor has no DACL")
            result = _advapi32.SetNamedSecurityInfoW(
                str(path.resolve()),
                _SE_FILE_OBJECT,
                _DACL_SECURITY_INFORMATION | _PROTECTED_DACL_SECURITY_INFORMATION,
                None,
                None,
                dacl,
                None,
            )
            if result:
                raise _result_error("SetNamedSecurityInfoW", result)
        finally:
            _kernel32.LocalFree(descriptor)

    def _ace_sid_address(address: int, ace_type: int, ace_size: int) -> int:
        offset = 8
        if ace_type in _OBJECT_ACE_TYPES:
            if ace_size < 12:
                raise PermissionError("sensitive file has a malformed object ACE")
            flags = wintypes.DWORD.from_address(address + 8).value
            offset = 12
            if flags & _ACE_OBJECT_TYPE_PRESENT:
                offset += 16
            if flags & _ACE_INHERITED_OBJECT_TYPE_PRESENT:
                offset += 16
        if offset >= ace_size:
            raise PermissionError("sensitive file has a malformed allow ACE")
        return address + offset

    def _validate_private_windows_acl(path: Path) -> None:
        owner = ctypes.c_void_p()
        dacl = ctypes.c_void_p()
        descriptor = ctypes.c_void_p()
        result = _advapi32.GetNamedSecurityInfoW(
            str(path.resolve()),
            _SE_FILE_OBJECT,
            _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION,
            ctypes.byref(owner),
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(descriptor),
        )
        if result:
            raise _result_error("GetNamedSecurityInfoW", result)
        try:
            _validate_windows_security_descriptor(owner, dacl, path)
        finally:
            if descriptor.value:
                _kernel32.LocalFree(descriptor)

    def _validate_private_windows_handle(descriptor: int, path: Path) -> None:
        import msvcrt

        windows_msvcrt: Any = msvcrt
        owner = ctypes.c_void_p()
        dacl = ctypes.c_void_p()
        security_descriptor = ctypes.c_void_p()
        handle = wintypes.HANDLE(windows_msvcrt.get_osfhandle(descriptor))
        result = _advapi32.GetSecurityInfo(
            handle,
            _SE_FILE_OBJECT,
            _OWNER_SECURITY_INFORMATION | _DACL_SECURITY_INFORMATION,
            ctypes.byref(owner),
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(security_descriptor),
        )
        if result:
            raise _result_error("GetSecurityInfo", result)
        try:
            _validate_windows_security_descriptor(owner, dacl, path)
        finally:
            if security_descriptor.value:
                _kernel32.LocalFree(security_descriptor)

    def _validate_windows_security_descriptor(
        owner: ctypes.c_void_p,
        dacl: ctypes.c_void_p,
        path: Path,
    ) -> None:
        trusted = set(_ALWAYS_TRUSTED_SIDS)
        trusted.add(_current_user_sid())
        if not owner.value or _sid_string(owner) not in trusted:
            raise PermissionError(
                f"sensitive file owner is outside the service trust boundary: {path}"
            )
        if not dacl.value:
            raise PermissionError(f"sensitive file has an unrestricted DACL: {path}")
        information = _ACL_SIZE_INFORMATION()
        if not _advapi32.GetAclInformation(
            dacl,
            ctypes.byref(information),
            ctypes.sizeof(information),
            _ACL_SIZE_INFORMATION_CLASS,
        ):
            raise _last_error("GetAclInformation")
        for index in range(information.AceCount):
            ace = ctypes.c_void_p()
            if not _advapi32.GetAce(dacl, index, ctypes.byref(ace)):
                raise _last_error("GetAce")
            assert ace.value is not None
            address = int(ace.value)
            header = _ACE_HEADER.from_address(address)
            if header.AceFlags & _INHERIT_ONLY_ACE:
                continue
            if header.AceType in _NON_GRANT_ACE_TYPES:
                continue
            if header.AceType not in _ALLOW_ACE_TYPES:
                raise PermissionError(
                    f"sensitive file has an unsupported access ACE: {path}"
                )
            mask = wintypes.DWORD.from_address(address + 4).value
            if not mask:
                continue
            sid_address = _ace_sid_address(address, header.AceType, header.AceSize)
            trustee = _sid_string(ctypes.c_void_p(sid_address))
            if trustee not in trusted:
                raise PermissionError(
                    "sensitive file grants access outside the service trust "
                    f"boundary: {path} ({trustee})"
                )
