"""Case selection and actual CLI evidence boundaries."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import TYPE_CHECKING

import acceptance
import verification
import verification_evidence
from acceptance import CaseResult
from project import ROOT
from verification_types import ExecutedTest, HostReport
from verification_types import TestOutcome as Outcome

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_selection_uses_ctest_labels_and_exact_names() -> None:
    # Given two labeled compiled assertions and one shared wire test.
    inventory = json.dumps(
        {
            "tests": [
                {"name": "core_TC11", "command": ["/bin/core", "--case", "TC11"]},
                {"name": "core_TC12", "command": ["/bin/core", "--case", "TC12"]},
                {
                    "name": "wire_boundary",
                    "command": ["/bin/wire"],
                    "properties": [{"name": "LABELS", "value": ["TC09", "TC44"]}],
                },
            ],
        },
    )
    # When a single registry case is selected.
    selected = verification.select_tests(inventory, ("TC11",))
    # Then its exact compiled assertion is selected.
    assert tuple(test.name for test in selected.tests) == ("core_TC11",)
    assert selected.unmapped == ()


def test_selection_reports_unmapped_case() -> None:
    # Given a compiled inventory containing a different case.
    inventory = '{"tests":[{"name":"core_TC11","command":["/bin/core"]}]}'
    # When a field case is requested on the host.
    selected = verification.select_tests(inventory, ("TC60",))
    # Then the requested case remains ready for its required test environment.
    assert selected.tests == ()
    assert selected.unmapped == ("TC60",)


def test_junit_retains_fixture_assertions_and_status(tmp_path: Path) -> None:
    # Given CTest JUnit containing one measured assertion result.
    junit = tmp_path / "junit.xml"
    _ = junit.write_text("""<testsuite><testcase name="core_TC11" status="run"><system-out>
{"case_id":"TC11","fixture":"sequence","scope":"host_core","result":"passed"}
</system-out></testcase></testsuite>""")
    # When the command's evidence is parsed.
    observed = verification.read_junit(junit)
    # Then the actual fixture identity and assertion result survive.
    assert observed[0].name == "core_TC11"
    assert observed[0].result == "passed"
    assert observed[0].fixtures[0].fixture == "sequence"


def test_cli_scopes_requested_case() -> None:
    # Given the real command entry and a compiled host fixture.
    command = [sys.executable, str(ROOT / "tools/cli.py"), "test", "--cases", "TC11"]
    # When the command executes the selected case.
    completed = subprocess.run(  # noqa: S603 - The fixture invokes the project's CLI argument vector.
        command, capture_output=True, text=True, check=False, timeout=120
    )
    # Then its report names only the selected compiled test and its actual JUnit evidence.
    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = HostReport.model_validate_json(completed.stdout.splitlines()[-1])
    assert report.requested_ids == ("TC11",)
    assert "core_TC11" in {test.name for test in report.tests}
    assert all(test.case_ids == ("TC11",) for test in report.tests)
    assert all(
        assertion.case_id == "TC11" for test in report.tests for assertion in test.outcome.fixtures
    )
    assert (ROOT / report.junit).is_file()


def test_cli_unmapped_and_unknown_cases_exit_two() -> None:
    # Given registered field and unrecognized case identifiers.
    for case_id in ("TC60", "TC99"):
        command = [sys.executable, str(ROOT / "tools/cli.py"), "test", "--cases", case_id]
        # When the real CLI resolves each requested case.
        completed = subprocess.run(  # noqa: S603 - Fixed registry identifiers exercise the real CLI.
            command, capture_output=True, text=True, check=False, timeout=120
        )
        # Then it requests the corresponding execution conditions.
        assert completed.returncode == 2, completed.stdout + completed.stderr


def test_host_observation_preserves_completed_device_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a completed device observation with collected operator evidence.
    monkeypatch.setattr(verification_evidence, "ROOT", tmp_path)
    monkeypatch.setattr(acceptance, "ROOT", tmp_path)
    device = tmp_path / "evidence/device/session.jsonl"
    device.parent.mkdir(parents=True)
    _ = device.write_text('{"operator_confirmation":"confirmed"}\n')
    previous = CaseResult(
        case_id="TC11",
        result="passed",
        mode="device",
        evidence=("evidence/device/session.jsonl",),
        operator="operator",
        reviewed_by="reviewer",
    )
    saved = tmp_path / "evidence/TC11/result.json"
    saved.parent.mkdir(parents=True)
    _ = saved.write_text(previous.model_dump_json())
    report = HostReport(
        run_id="fixture",
        observed_at="2026-11-17T18:00:00+08:00",
        requested_ids=("TC11",),
        mode="host",
        exit_code=0,
        status="executed",
        tests=(
            ExecutedTest(
                name="core_TC11",
                command=("fixture-binary",),
                case_ids=("TC11",),
                executable_sha256="a" * 64,
                outcome=Outcome(name="core_TC11", result="passed", stdout="fixture"),
            ),
        ),
        junit="evidence/host/junit.xml",
        report="evidence/host/report.json",
    )
    # When fresh host component evidence is retained.
    verification_evidence.retain_case_observations(report)
    # Then the completed device record survives and the new assertion remains traceable.
    assert saved.read_text() == previous.model_dump_json()
    observation = verification_evidence.CaseObservation.model_validate_json(
        (tmp_path / "evidence/TC11/host/fixture.json").read_text()
    )
    assert observation.preserved_complete_result is True
    assert observation.result == "partial"


def test_machine_fixture_failure_controls_junit_outcome(tmp_path: Path) -> None:
    # Given a subprocess that writes a failed assertion into its retained output.
    junit = tmp_path / "junit.xml"
    _ = junit.write_text("""<testsuite><testcase name="core_TC11" status="run"><system-out>
{"case_id":"TC11","fixture":"sequence","result":"failed"}
</system-out></testcase></testsuite>""")
    # When its JUnit record is interpreted.
    outcome = verification.read_junit(junit)
    # Then the failed assertion determines the observed result.
    assert outcome[0].result == "failed"
