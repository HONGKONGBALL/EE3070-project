"""Release snapshots retain the final persisted progress and checkpoint bytes."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Literal
from zipfile import ZipFile

import acceptance
import packaging_release
import progress_store
import project
import pytest
from acceptance import Registry, Summary
from packaging_release import Manifest
from progress_store import Progress, TaskProgress

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def snapshot_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # Given: a temporary project with real progress and summary persistence.
    for module in (project, acceptance, progress_store, packaging_release):
        monkeypatch.setattr(module, "ROOT", tmp_path)
    registry = tmp_path / "config/test_registry.json"
    registry.parent.mkdir()
    _ = registry.write_text(Registry(cases=()).model_dump_json(), encoding="utf-8")
    progress = Progress(
        updated_at="2026-11-18T00:00:00+08:00",
        tasks=(TaskProgress(task_id="T24"),),
    )
    _ = (tmp_path / "progress.json").write_text(progress.model_dump_json(), encoding="utf-8")
    progress_store.checkpoint()
    return tmp_path


@pytest.mark.parametrize("filename", ["progress.json", "checkpoint.md"])
def test_package_matches_persisted_progress_in_zip_and_manifests(
    snapshot_workspace: Path, filename: Literal["progress.json", "checkpoint.md"]
) -> None:
    before = (snapshot_workspace / filename).read_bytes()
    # When: the public package operation creates the real snapshot archive.
    result = packaging_release.package()
    # Then: its final progress bytes and recorded identities describe the same files.
    assert result == 0
    current = (snapshot_workspace / filename).read_bytes()
    assert current != before
    manifest = Manifest.model_validate_json(
        (snapshot_workspace / "release/manifest.json").read_bytes()
    )
    archive_path = snapshot_workspace / "release/navigation_24h.zip"
    with ZipFile(archive_path) as archive:
        assert archive.read(filename) == current
        embedded = Manifest.model_validate_json(archive.read("release/manifest.json"))
    entry = next(item for item in manifest.files if item.path == filename)
    assert entry.sha256 == hashlib.sha256(current).hexdigest()
    assert entry.bytes == len(current)
    assert embedded.files == manifest.files
    assert manifest.archive_sha256 == hashlib.sha256(archive_path.read_bytes()).hexdigest()
    task = progress_store.load_progress().tasks[0]
    assert (task.task_id, task.status, task.completion) == ("T24", "running", 30)


def test_archive_preserves_evidence_raw_capture_selection(tmp_path: Path) -> None:
    # Given: RX/TX evidence captures alongside project and generated-directory fixtures.
    captures = (
        ("evidence/T10/runtime/rx.raw", b"RX capture\r\n"),
        ("evidence/T10/runtime/tx.raw", b"TX capture\n"),
    )
    for name, data in (
        *captures,
        ("tools/capture.raw", b"tool fixture"),
        ("docs/evidence/capture.raw", b"document fixture"),
        ("evidence/build/capture.raw", b"build fixture"),
        ("evidence/.build/capture.raw", b"cache fixture"),
        ("evidence/DerivedData/capture.raw", b"derived fixture"),
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_bytes(data)
    destination = tmp_path / "release/project.zip"
    summary = Summary(total=0, passed=0, failed=0, partial=0, awaiting=0)
    # When: the real archive captures the selected project evidence.
    manifest = packaging_release.archive_project(tmp_path, destination, summary)
    # Then: ZIP contents and manifest identities match the evidence capture set.
    expected = {name for name, _data in captures}
    assert {entry.path for entry in manifest.files} == expected
    with ZipFile(destination) as archive:
        assert set(archive.namelist()) == expected | {"release/manifest.json"}
        for name, data in captures:
            assert archive.read(name) == data
            entry = next(item for item in manifest.files if item.path == name)
            assert entry.sha256 == hashlib.sha256(data).hexdigest()
            assert entry.bytes == len(data)
