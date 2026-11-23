"""Engineering snapshot archive with source and evidence file identities."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Final
from zipfile import ZIP_DEFLATED, ZipFile

from acceptance import Summary, write_summary
from progress_store import update
from project import ROOT, Model, digest, now, save

if TYPE_CHECKING:
    from pathlib import Path

SOURCE_ROOTS: Final = (
    "tools",
    "firmware",
    "ios",
    "pico",
    "protocol",
    "config",
    "fixtures",
    "tests",
    "docs",
    "evidence",
)
SOURCE_FILES: Final = (
    "CMakeLists.txt",
    "pyproject.toml",
    "uv.lock",
    "progress.json",
    "checkpoint.md",
    "task_plan.md",
    "progress.md",
    "findings.md",
    "README.md",
    ".gitignore",
)
SOURCE_SUFFIXES: Final = {
    ".py",
    ".cpp",
    ".c",
    ".hpp",
    ".h",
    ".swift",
    ".md",
    ".txt",
    ".json",
    ".jsonl",
    ".xml",
    ".log",
    ".toml",
    ".lock",
    ".cmake",
    ".plist",
    ".pbxproj",
    ".xcscheme",
    ".csv",
    ".defaults",
}
GENERATED_PARTS: Final = {
    ".toolchain",
    ".venv",
    ".build",
    "build",
    "DerivedData",
    "DerivedDataUnsigned",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "xcuserdata",
}


class FileEntry(Model):
    """One archived file with SHA-256 and byte length."""

    path: str
    sha256: str
    bytes: int


class Manifest(Model):
    """Source, artifact and evidence inventory at snapshot time."""

    generated_at: str
    kind: str = "engineering_snapshot"
    files: tuple[FileEntry, ...]
    acceptance: Summary
    archive_sha256: str | None = None


def snapshot_paths(root: Path) -> tuple[Path, ...]:
    """Select project sources, configuration examples and collected evidence."""
    selected: set[Path] = {root / name for name in SOURCE_FILES if (root / name).is_file()}
    for directory in SOURCE_ROOTS:
        for path in (root / directory).rglob("*"):
            relative = path.relative_to(root)
            if not path.is_file() or path.is_symlink() or set(relative.parts) & GENERATED_PARTS:
                continue
            if directory == "config" and path.name not in {
                "local.example.json",
                "field_routes.json",
                "soak_schedule.json",
                "test_registry.json",
                "task_catalog.json",
                "secrets.example.env",
            }:
                continue
            if (
                path.suffix in SOURCE_SUFFIXES
                or (directory == "evidence" and path.suffix == ".raw")
                or path.name
                in {
                    "sdkconfig.defaults",
                    "CMakeLists.txt",
                    ".gitignore",
                    "LICENSE",
                    "LICENSE.md",
                    "secrets.example.env",
                }
            ):
                selected.add(path)
    for directory in (
        root / "firmware/build",
        root / "ios/DerivedData/Build/Products/Debug-iphoneos",
        root / "ios/DerivedDataUnsigned/Build/Products/Debug-iphoneos",
        root / "ios/.build/iOSValidation/Build/Products/Debug-iphonesimulator",
    ):
        for path in directory.rglob("*"):
            if (
                path.is_file()
                and not path.is_symlink()
                and (
                    path.suffix in {".bin", ".elf", ".map"}
                    or any(part.endswith(".app") for part in path.parts)
                )
            ):
                selected.add(path)
    return tuple(sorted(selected))


def archive_project(root: Path, destination: Path, summary: Summary) -> Manifest:
    """Write a reviewable archive and an independent archive hash manifest."""
    paths = snapshot_paths(root)
    entries: list[FileEntry] = []
    destination.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(destination, "w", compression=ZIP_DEFLATED) as archive:
        for path in paths:
            data = path.read_bytes()
            relative = str(path.relative_to(root))
            archive.writestr(relative, data)
            entries.append(
                FileEntry(path=relative, sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
            )
        manifest = Manifest(generated_at=now(), files=tuple(entries), acceptance=summary)
        archive.writestr("release/manifest.json", manifest.model_dump_json(indent=2))
    return manifest.model_copy(update={"archive_sha256": digest(destination)})


def package() -> int:
    """Archive the current engineering state and observed acceptance coverage."""
    update(
        "T24",
        "running",
        30,
        (),
        ("release/navigation_24h.zip", "release/manifest.json"),
        "核对用例、设备构建、安装、现场记录及审核签核",
    )
    summary = write_summary()
    destination = ROOT / "release/navigation_24h.zip"
    manifest = archive_project(ROOT, destination, summary)
    save(ROOT / "release/manifest.json", manifest)
    print(
        f"Snapshot: {destination} · {len(manifest.files)} files · SHA-256 {manifest.archive_sha256}"
    )
    return 0
