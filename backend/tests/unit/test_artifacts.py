"""Security and immutability tests for private Source Artifact bytes."""

import hashlib
import os
import stat
from pathlib import Path

import pytest

from founderlookup.infrastructure.artifacts import (
    ArtifactAccessDeniedError,
    ArtifactConflictError,
    ArtifactIntegrityError,
    ArtifactNotFoundError,
    InvalidArtifactReferenceError,
    PrivateArtifactStore,
)

ARTIFACT_ID = "artifact_opaque_01"
PRINCIPAL_ID = "investor_01"
CONTENT = b"private founder deck bytes"
CONTENT_SHA256 = hashlib.sha256(CONTENT).hexdigest()


def _store(root: Path, *, allow: bool = True) -> PrivateArtifactStore:
    return PrivateArtifactStore(
        root.resolve(),
        authorize_read=lambda principal_id, artifact_id: (
            allow and principal_id == PRINCIPAL_ID and artifact_id == ARTIFACT_ID
        ),
    )


def test_put_is_atomic_private_and_idempotent_for_identical_content(tmp_path: Path) -> None:
    root = tmp_path / "private-artifacts"
    store = _store(root)

    first = store.put(ARTIFACT_ID, CONTENT, expected_sha256=CONTENT_SHA256)
    second = store.put(ARTIFACT_ID, CONTENT, expected_sha256=CONTENT_SHA256)

    assert first == second
    assert first.content_sha256 == CONTENT_SHA256
    assert first.size_bytes == len(CONTENT)
    assert not hasattr(first, "path")
    assert (
        store.read(
            ARTIFACT_ID,
            principal_id=PRINCIPAL_ID,
            expected_sha256=CONTENT_SHA256,
        )
        == CONTENT
    )
    blobs = tuple(root.rglob("*.blob"))
    assert len(blobs) == 1
    assert ARTIFACT_ID not in str(blobs[0])
    assert tuple(root.rglob(".pending-*")) == ()
    if os.name == "posix":
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        assert stat.S_IMODE(blobs[0].stat().st_mode) == 0o600


def test_reusing_an_artifact_id_for_different_bytes_is_a_conflict(tmp_path: Path) -> None:
    store = _store(tmp_path / "private-artifacts")
    store.put(ARTIFACT_ID, CONTENT, expected_sha256=CONTENT_SHA256)
    replacement = b"different deck bytes"

    with pytest.raises(ArtifactConflictError, match="different bytes"):
        store.put(
            ARTIFACT_ID,
            replacement,
            expected_sha256=hashlib.sha256(replacement).hexdigest(),
        )

    assert (
        store.read(
            ARTIFACT_ID,
            principal_id=PRINCIPAL_ID,
            expected_sha256=CONTENT_SHA256,
        )
        == CONTENT
    )


def test_write_rejects_hash_mismatch_without_installing_bytes(tmp_path: Path) -> None:
    root = tmp_path / "private-artifacts"
    store = _store(root)

    with pytest.raises(ArtifactIntegrityError, match="does not match"):
        store.put(ARTIFACT_ID, CONTENT, expected_sha256="0" * 64)

    assert tuple(root.rglob("*.blob")) == ()
    assert tuple(root.rglob(".pending-*")) == ()


def test_read_requires_policy_authorization_and_never_accepts_a_path(tmp_path: Path) -> None:
    root = tmp_path / "private-artifacts"
    store = _store(root, allow=False)
    store.put(ARTIFACT_ID, CONTENT, expected_sha256=CONTENT_SHA256)

    with pytest.raises(ArtifactAccessDeniedError, match="not authorized"):
        store.read(
            ARTIFACT_ID,
            principal_id=PRINCIPAL_ID,
            expected_sha256=CONTENT_SHA256,
        )
    with pytest.raises(InvalidArtifactReferenceError, match="opaque identifier"):
        store.read(
            "../outside/private.txt",
            principal_id=PRINCIPAL_ID,
            expected_sha256=CONTENT_SHA256,
        )
    assert not (tmp_path / "outside" / "private.txt").exists()


def test_denied_read_does_not_create_an_artifact_bucket(tmp_path: Path) -> None:
    root = tmp_path / "private-artifacts"
    store = _store(root, allow=False)

    with pytest.raises(ArtifactAccessDeniedError):
        store.read(
            "valid-but-unknown-artifact",
            principal_id=PRINCIPAL_ID,
            expected_sha256=CONTENT_SHA256,
        )

    assert tuple(root.iterdir()) == ()


def test_authorized_missing_artifact_is_explicit(tmp_path: Path) -> None:
    store = PrivateArtifactStore(
        (tmp_path / "private-artifacts").resolve(),
        authorize_read=lambda _principal_id, _artifact_id: True,
    )

    with pytest.raises(ArtifactNotFoundError, match="does not exist"):
        store.read(
            ARTIFACT_ID,
            principal_id=PRINCIPAL_ID,
            expected_sha256=CONTENT_SHA256,
        )


def test_tampering_is_detected_before_bytes_are_returned(tmp_path: Path) -> None:
    root = tmp_path / "private-artifacts"
    store = _store(root)
    store.put(ARTIFACT_ID, CONTENT, expected_sha256=CONTENT_SHA256)
    blob = next(root.rglob("*.blob"))
    blob.write_bytes(b"tampered")

    with pytest.raises(ArtifactIntegrityError, match="failed SHA-256"):
        store.read(
            ARTIFACT_ID,
            principal_id=PRINCIPAL_ID,
            expected_sha256=CONTENT_SHA256,
        )


def test_symlinked_hash_bucket_cannot_redirect_a_write(tmp_path: Path) -> None:
    root = tmp_path / "private-artifacts"
    outside = tmp_path / "outside"
    outside.mkdir()
    store = _store(root)
    storage_key = hashlib.sha256(ARTIFACT_ID.encode()).hexdigest()
    (root / storage_key[:2]).symlink_to(outside, target_is_directory=True)

    with pytest.raises(ArtifactIntegrityError, match="bucket"):
        store.put(ARTIFACT_ID, CONTENT, expected_sha256=CONTENT_SHA256)

    assert tuple(outside.iterdir()) == ()
    assert tuple(root.rglob(".pending-*")) == ()


def test_bucket_replacement_race_fails_closed_without_redirecting_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "private-artifacts"
    outside = tmp_path / "outside"
    outside.mkdir()
    store = _store(root)
    storage_key = hashlib.sha256(ARTIFACT_ID.encode()).hexdigest()
    bucket = root / storage_key[:2]
    displaced_bucket = root / f"{storage_key[:2]}-displaced"

    def replace_bucket(_artifact_id: str) -> None:
        bucket.rename(displaced_bucket)
        bucket.symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(store, "_before_blob_install", replace_bucket)

    with pytest.raises(ArtifactIntegrityError, match="bucket"):
        store.put(ARTIFACT_ID, CONTENT, expected_sha256=CONTENT_SHA256)

    assert tuple(outside.iterdir()) == ()
    assert tuple(displaced_bucket.glob("*.blob")) == ()
    assert tuple(displaced_bucket.glob(".pending-*")) == ()
