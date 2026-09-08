"""Windows DACL helpers for sensitive service files and directories."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


def secure_private_path(path: Path, *, directory: bool | None = None) -> None:
    """Restrict a Windows path to the service identity, SYSTEM, and admins."""

    if os.name != "nt":
        return
    is_directory = path.is_dir() if directory is None else directory
    _set_private_windows_acl(path, directory=is_directory)


def validate_private_path(path: Path) -> None:
    """Reject a Windows path whose owner or allow ACEs cross the trust boundary."""

    if os.name != "nt":
        return
    _validate_private_windows_acl(path)


if os.name == "nt":  # pragma: no cover - exercised by the Windows CI matrix
    import ctypes
    from ctypes import wintypes

    _SDDL_REVISION_1 = 1
    _SE_FILE_OBJECT = 1
    _OWNER_SECURITY_INFORMATION = 0x00000001
    _DACL_SECURITY_INFORMATION = 0x00000004
    _PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
    _ACL_SIZE_INFORMATION_CLASS = 2
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

    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]

    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
    _kernel32.LocalFree.restype = ctypes.c_void_p

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
            trusted = set(_ALWAYS_TRUSTED_SIDS)
            trusted.add(_current_user_sid())
            if not owner.value or _sid_string(owner) not in trusted:
                raise PermissionError(
                    f"sensitive file owner is outside the service trust boundary: {path}"
                )
            if not dacl.value:
                raise PermissionError(
                    f"sensitive file has an unrestricted DACL: {path}"
                )
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
        finally:
            if descriptor.value:
                _kernel32.LocalFree(descriptor)
