"""Source-scoped validation commands and acceptance evidence entry points."""

from __future__ import annotations

import os
import sys
from typing import Final, Literal, assert_never

from acceptance import has_evidence, registry, results, write_summary
from progress_store import update
from project import ROOT, Model, executable, now, run, save
from verification_host import execute_host
from verification_types import read_junit, select_tests
from wire_models import FRAME_ADAPTER, schema

__all__ = ("check_task", "read_junit", "regression", "select_tests", "test_cases")
EXPECTED_CASES: Final = 60
EXPECTED_REQUIREMENTS: Final = 24


class PathCheck(Model):
    """Observed project contract path."""

    path: str
    exists: bool


class StructureReport(Model):
    """T02 protocol, registry and project structure observations."""

    observed_at: str
    paths: tuple[PathCheck, ...]
    schema_sample: Literal["passed"]
    registry_cases: int
    registry_unique_ids: int
    requirement_ids: tuple[str, ...]


class DeviceSteps(Model):
    """Source expectation and evidence collection inputs for one device case."""

    case_id: str
    expected: str
    name: str
    status: Literal["waiting_condition"] = "waiting_condition"
    required: tuple[str, ...] = (
        "device_logs",
        "app_firmware_hashes",
        "config_version",
        "operator_confirmation",
    )


def regression(suite: Literal["host", "all"]) -> int:
    """Run current host assertions, Python tools and the shared Swift protocol checks."""
    report = execute_host(None)
    python = run([sys.executable, "-m", "pytest", str(ROOT / "tests/python"), "-q"], "host")
    environment = dict(os.environ)
    environment["CLANG_MODULE_CACHE_PATH"] = str(ROOT / "ios/.build/cache")
    swift = run(
        [
            executable("swift"),
            "run",
            "--disable-sandbox",
            "--package-path",
            str(ROOT / "ios"),
            "--scratch-path",
            str(ROOT / "ios/.build"),
            "--cache-path",
            str(ROOT / "ios/.build/swiftpm-cache"),
            "--config-path",
            str(ROOT / "ios/.build/swiftpm-config"),
            "--security-path",
            str(ROOT / "ios/.build/swiftpm-security"),
            "ProtocolChecks",
        ],
        "ios-host",
        timeout=300,
        env=environment,
    )
    code = max(report.exit_code, python.exit_code, swift.exit_code)
    summary = write_summary()
    update(
        "T02",
        "running" if code == 0 else "waiting_condition",
        85 if code == 0 else 60,
        (*report.records, python, swift),
        ("protocol/navigation.schema.json", report.junit, report.report),
        "核对跨端协议并按设备配置进行端到端验证",
    )
    print(f"host: {report.status} · tests={len(report.tests)} · report={report.report}")
    print(summary.model_dump_json())
    match suite:
        case "host":
            return code
        case "all":
            return max(2, code) if summary.passed < summary.total else code
        case unreachable:
            assert_never(unreachable)


def check_task(task: str) -> int:
    """Generate protocol schema and verify T02's concrete engineering contracts."""
    if task != "T02":
        print(f"任务 {task} 的执行入口见 config/task_catalog.json")
        return 2
    path = ROOT / "protocol/navigation.schema.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(schema() + "\n")
    sample = """{
        "type":"location", "phone_session_id":"fixture", "fix_seq":1,
        "source_timestamp_ms":1000, "measurement_age_ms":20, "coord_frame":"WGS84",
        "lng":114.17, "lat":22.33, "horizontal_accuracy_m":5
    }"""
    _ = FRAME_ADAPTER.validate_json(sample)
    files = (
        "ios/NavigationCompanion.xcodeproj/project.pbxproj",
        "firmware/CMakeLists.txt",
        "firmware/components/nav_core/CMakeLists.txt",
        "config/test_registry.json",
        "tools/navctl.py",
        "protocol/navigation.schema.json",
        "tests/host/CMakeLists.txt",
    )
    definitions = registry().cases
    report = StructureReport(
        observed_at=now(),
        paths=tuple(PathCheck(path=file, exists=(ROOT / file).is_file()) for file in files),
        schema_sample="passed",
        registry_cases=len(definitions),
        registry_unique_ids=len({case.id for case in definitions}),
        requirement_ids=tuple(
            sorted({item for case in definitions for item in case.requirement_ids})
        ),
    )
    save(ROOT / "evidence/T02/structure.json", report)
    ready = (
        all(item.exists for item in report.paths)
        and report.registry_cases == EXPECTED_CASES
        and (
            report.registry_unique_ids == EXPECTED_CASES
            and len(report.requirement_ids) == EXPECTED_REQUIREMENTS
        )
    )
    return regression("host") if ready else 2


def _device_steps(ids: tuple[str, ...]) -> None:
    definitions = {case.id: case for case in registry().cases}
    for case_id in ids:
        definition = definitions[case_id]
        save(
            ROOT / "evidence" / case_id / "device_steps.json",
            DeviceSteps(case_id=case_id, expected=definition.expected_result, name=definition.name),
        )


def test_cases(ids: tuple[str, ...], mode: Literal["host", "device", "mixed"]) -> int:
    """Execute requested assertions and identify their actual collection environment."""
    definitions = {case.id: case for case in registry().cases}
    unknown = any(case_id not in definitions for case_id in ids)
    if unknown:
        report = execute_host(ids)
        print(report.model_dump_json(exclude={"records"}))
        return report.exit_code
    match mode:
        case "host":
            report = execute_host(ids)
            print(report.model_dump_json(exclude={"records"}))
            return report.exit_code
        case "device":
            _device_steps(ids)
            completed = {
                case.case_id
                for case in results()
                if case.result == "passed"
                and case.mode in {"device", "mixed"}
                and has_evidence(case)
            }
            return 0 if set(ids) <= completed else 2
        case "mixed":
            report = execute_host(ids)
            _device_steps(ids)
            completed = {
                case.case_id
                for case in results()
                if case.result == "passed"
                and case.mode in {"device", "mixed"}
                and has_evidence(case)
            }
            print(report.model_dump_json(exclude={"records"}))
            return report.exit_code if set(ids) <= completed else max(2, report.exit_code)
        case unreachable:
            assert_never(unreachable)
