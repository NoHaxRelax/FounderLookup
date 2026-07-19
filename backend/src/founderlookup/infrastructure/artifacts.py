"""Private, content-verified storage for immutable Source Artifact bytes."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

_STABLE_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")


class ArtifactStoreError(RuntimeError):
    """Base error for protected Source Artifact storage."""


class InvalidArtifactReferenceError(ArtifactStoreError, ValueError):
    """An identifier or digest cannot safely address a server-owned artifact."""


class ArtifactConflictError(ArtifactStoreError):
    """An immutable artifact identifier was reused for different bytes."""


class ArtifactNotFoundError(ArtifactStoreError):
    """No stored bytes exist for an artifact identifier."""


class ArtifactAccessDeniedError(ArtifactStoreError, PermissionError):
    """The configured policy did not authorize access to private bytes."""


class ArtifactIntegrityError(ArtifactStoreError):
    """Stored bytes do not match their Source Artifact content hash."""


class ArtifactReadAuthorizer(Protocol):
    """Server-side policy invoked for every artifact byte read."""

    def __call__(self, principal_id: str, artifact_id: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    """Safe metadata returned without exposing a local filesystem path."""

    artifact_id: str
    content_sha256: str
    size_bytes: int


def _validate_id(value: str, *, field: str) -> str:
    if _STABLE_ID_PATTERN.fullmatch(value) is None:
        raise InvalidArtifactReferenceError(f"{field} must be a stable opaque identifier")
    return value


def _validate_sha256(value: str) -> str:
    if _SHA256_PATTERN.fullmatch(value) is None:
        raise InvalidArtifactReferenceError("expected_sha256 must be a lowercase SHA-256 digest")
    return value


class PrivateArtifactStore:
    """Append-only byte store addressed only through validated opaque identifiers."""

    def __init__(self, root: Path, *, authorize_read: ArtifactReadAuthorizer) -> None:
        self._require_secure_directory_primitives()
        if not root.is_absolute():
            raise ValueError("artifact root must be an absolute server-controlled path")
        if root.is_symlink():
            raise ValueError("artifact root cannot be a symbolic link")
        if root.exists() and not root.is_dir():
            raise ValueError("artifact root must be a directory")
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._root = root
        root_descriptor = self._open_directory_path(root)
        try:
            os.fchmod(root_descriptor, 0o700)
            root_status = os.fstat(root_descriptor)
            if not stat.S_ISDIR(root_status.st_mode):  # pragma: no cover - O_DIRECTORY enforces
                raise ValueError("artifact root must be a directory")
            self._root_identity = (root_status.st_dev, root_status.st_ino)
        finally:
            os.close(root_descriptor)
        self._authorize_read = authorize_read

    @staticmethod
    def _require_secure_directory_primitives() -> None:
        required_dir_fd_functions = (os.open, os.mkdir, os.unlink, os.link)
        if any(function not in os.supports_dir_fd for function in required_dir_fd_functions):
            raise RuntimeError(
                "private artifact storage requires directory-relative filesystem operations"
            )
        if os.link not in os.supports_follow_symlinks:
            raise RuntimeError("private artifact storage requires no-follow hard-link support")
        if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
            raise RuntimeError("private artifact storage requires no-follow directory handles")

    @staticmethod
    def _directory_open_flags() -> int:
        return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)

    @staticmethod
    def _file_open_flags() -> int:
        return os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)

    @classmethod
    def _open_directory_path(cls, path: Path) -> int:
        try:
            return os.open(path, cls._directory_open_flags())
        except OSError as error:
            raise ValueError("artifact root failed directory safety verification") from error

    @contextmanager
    def _root_descriptor(self) -> Iterator[int]:
        try:
            descriptor = os.open(self._root, self._directory_open_flags())
        except OSError as error:
            raise ArtifactIntegrityError(
                "artifact root failed directory safety verification"
            ) from error
        try:
            current = os.fstat(descriptor)
            if (current.st_dev, current.st_ino) != self._root_identity:
                raise ArtifactIntegrityError("artifact root identity changed")
            yield descriptor
        finally:
            os.close(descriptor)

    @staticmethod
    def _storage_names(artifact_id: str) -> tuple[str, str]:
        _validate_id(artifact_id, field="artifact_id")
        storage_key = hashlib.sha256(artifact_id.encode("utf-8")).hexdigest()
        return storage_key[:2], f"{storage_key}.blob"

    @contextmanager
    def _bucket_descriptor(
        self,
        root_descriptor: int,
        bucket_name: str,
        *,
        create: bool,
    ) -> Iterator[tuple[int, tuple[int, int]]]:
        if create:
            try:
                os.mkdir(bucket_name, mode=0o700, dir_fd=root_descriptor)
            except FileExistsError:
                pass
            except OSError as error:
                raise ArtifactIntegrityError(
                    "artifact bucket could not be created safely"
                ) from error
        try:
            descriptor = os.open(
                bucket_name,
                self._directory_open_flags(),
                dir_fd=root_descriptor,
            )
        except FileNotFoundError as error:
            if create:
                raise ArtifactIntegrityError(
                    "artifact bucket disappeared during creation"
                ) from error
            raise
        except OSError as error:
            raise ArtifactIntegrityError(
                "artifact bucket failed directory safety verification"
            ) from error
        try:
            os.fchmod(descriptor, 0o700)
            bucket_status = os.fstat(descriptor)
            if not stat.S_ISDIR(bucket_status.st_mode):  # pragma: no cover - O_DIRECTORY enforces
                raise ArtifactIntegrityError("artifact bucket is not a directory")
            identity = (bucket_status.st_dev, bucket_status.st_ino)
            yield descriptor, identity
        finally:
            os.close(descriptor)

    def _verify_bucket_binding(
        self,
        root_descriptor: int,
        bucket_name: str,
        expected_identity: tuple[int, int],
    ) -> None:
        try:
            descriptor = os.open(
                bucket_name,
                self._directory_open_flags(),
                dir_fd=root_descriptor,
            )
        except OSError as error:
            raise ArtifactIntegrityError("artifact bucket binding changed") from error
        try:
            current = os.fstat(descriptor)
            if (current.st_dev, current.st_ino) != expected_identity:
                raise ArtifactIntegrityError("artifact bucket identity changed")
        finally:
            os.close(descriptor)

    def _verify_root_binding(self) -> None:
        with self._root_descriptor():
            return

    @staticmethod
    def _create_pending_file(bucket_descriptor: int) -> tuple[str, int]:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        for _attempt in range(128):
            pending_name = f".pending-{os.urandom(16).hex()}"
            try:
                descriptor = os.open(
                    pending_name,
                    flags,
                    0o600,
                    dir_fd=bucket_descriptor,
                )
            except FileExistsError:
                continue
            except OSError as error:
                raise ArtifactIntegrityError(
                    "artifact pending file could not be created safely"
                ) from error
            return pending_name, descriptor
        raise ArtifactIntegrityError("artifact pending filename budget was exhausted")

    def _before_blob_install(self, _artifact_id: str) -> None:
        """Internal synchronization hook used by deterministic race regression tests."""

        return None

    def put(
        self,
        artifact_id: str,
        content: bytes,
        *,
        expected_sha256: str,
    ) -> StoredArtifact:
        """Atomically create immutable bytes after checking caller-supplied metadata."""

        expected = _validate_sha256(expected_sha256)
        actual = hashlib.sha256(content).hexdigest()
        if actual != expected:
            raise ArtifactIntegrityError("artifact content does not match expected SHA-256")
        bucket_name, target_name = self._storage_names(artifact_id)

        with (
            self._root_descriptor() as root_descriptor,
            self._bucket_descriptor(
                root_descriptor,
                bucket_name,
                create=True,
            ) as (bucket_descriptor, bucket_identity),
        ):
            pending_name, pending_descriptor = self._create_pending_file(bucket_descriptor)
            target_created = False
            try:
                try:
                    os.fchmod(pending_descriptor, 0o600)
                    with os.fdopen(pending_descriptor, "wb") as pending:
                        pending.write(content)
                        pending.flush()
                        os.fsync(pending.fileno())
                except BaseException:
                    with suppress(OSError):
                        os.close(pending_descriptor)
                    raise

                self._before_blob_install(artifact_id)
                try:
                    # Both names resolve below the already-opened, no-follow bucket.
                    os.link(
                        pending_name,
                        target_name,
                        src_dir_fd=bucket_descriptor,
                        dst_dir_fd=bucket_descriptor,
                        follow_symlinks=False,
                    )
                    target_created = True
                except FileExistsError:
                    existing = self._read_file_at(bucket_descriptor, target_name)
                    if existing != content:
                        raise ArtifactConflictError(
                            f"artifact {artifact_id} already stores different bytes"
                        ) from None
                except OSError as error:
                    raise ArtifactIntegrityError(
                        "artifact could not be installed safely"
                    ) from error

                self._verify_bucket_binding(
                    root_descriptor,
                    bucket_name,
                    bucket_identity,
                )
                self._verify_root_binding()
                self._sync_directory_descriptor(bucket_descriptor)
            except BaseException:
                if target_created:
                    with suppress(OSError):
                        os.unlink(target_name, dir_fd=bucket_descriptor)
                raise
            finally:
                with suppress(FileNotFoundError):
                    os.unlink(pending_name, dir_fd=bucket_descriptor)

        return StoredArtifact(
            artifact_id=artifact_id,
            content_sha256=actual,
            size_bytes=len(content),
        )

    def read(
        self,
        artifact_id: str,
        *,
        principal_id: str,
        expected_sha256: str,
    ) -> bytes:
        """Return verified bytes only after the injected server policy authorizes them."""

        _validate_id(principal_id, field="principal_id")
        _validate_id(artifact_id, field="artifact_id")
        expected = _validate_sha256(expected_sha256)
        if not self._authorize_read(principal_id, artifact_id):
            raise ArtifactAccessDeniedError("principal is not authorized to read this artifact")
        bucket_name, target_name = self._storage_names(artifact_id)
        try:
            with (
                self._root_descriptor() as root_descriptor,
                self._bucket_descriptor(
                    root_descriptor,
                    bucket_name,
                    create=False,
                ) as (bucket_descriptor, _identity),
            ):
                return self._read_verified_at(bucket_descriptor, target_name, expected)
        except FileNotFoundError as error:
            raise ArtifactNotFoundError(f"artifact {artifact_id} does not exist") from error

    @staticmethod
    def _read_verified_at(
        bucket_descriptor: int,
        target_name: str,
        expected_sha256: str,
    ) -> bytes:
        content = PrivateArtifactStore._read_file_at(bucket_descriptor, target_name)
        actual = hashlib.sha256(content).hexdigest()
        if actual != expected_sha256:
            raise ArtifactIntegrityError("stored artifact failed SHA-256 verification")
        return content

    @staticmethod
    def _read_file_at(bucket_descriptor: int, target_name: str) -> bytes:
        try:
            descriptor = os.open(
                target_name,
                PrivateArtifactStore._file_open_flags(),
                dir_fd=bucket_descriptor,
            )
        except FileNotFoundError:
            raise
        except OSError as error:
            raise ArtifactIntegrityError(
                "artifact entry failed file safety verification"
            ) from error
        try:
            file_status = os.fstat(descriptor)
            if not stat.S_ISREG(file_status.st_mode):
                raise ArtifactIntegrityError("artifact entry is not a regular file")
            with os.fdopen(descriptor, "rb") as artifact_file:
                return artifact_file.read()
        except BaseException:
            with suppress(OSError):
                os.close(descriptor)
            raise

    @staticmethod
    def _sync_directory_descriptor(descriptor: int) -> None:
        with suppress(OSError):
            os.fsync(descriptor)
