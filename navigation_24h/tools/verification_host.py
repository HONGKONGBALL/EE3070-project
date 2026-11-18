"""Compile once, execute selected CTest assertions and retain their provenance."""

from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from uuid import uuid4

from acceptance import registry
from project import ROOT, Model, RunRecord, digest, executable, now, run, save
from pydantic import ValidationError
from verification_evidence import retain_case_observations
from verification_types import (
    ExecutedTest,
    HostReport,
    Inventory,
    Selection,
    read_junit,
    select_tests,
)


class PreparedHost(Model):
    """Actual configure, build and inventory command evidence."""

    records: tuple[RunRecord, ...]
    inventory: str = ""
    exit_code: int


def prepare_host() -> PreparedHost:
    """Configure and compile available host binaries once before selecting tests."""
    environment = dict(os.environ)
    commands = (
        [
            executable("cmake"),
            "-S",
            str(ROOT),
            "-B",
            str(ROOT / "build/host"),
            "-G",
            "Ninja",
            f"-DCMAKE_MAKE_PROGRAM={executable('ninja')}",
        ],
        [executable("cmake"), "--build", str(ROOT / "build/host")],
        [executable("ctest"), "--test-dir", str(ROOT / "build/host"), "--show-only=json-v1"],
    )
    records: list[RunRecord] = []
    for command in commands:
        record = run(command, "host", env=environment)
        records.append(record)
        if record.exit_code:
            return PreparedHost(records=tuple(records), exit_code=record.exit_code)
    return PreparedHost(records=tuple(records), inventory=records[-1].stdout, exit_code=0)


def _publish(report: HostReport) -> HostReport:
    save(ROOT / report.report, report)
    save(ROOT / "evidence/host/latest_report.json", report)
    retain_case_observations(report)
    return report


def execute_host(requested: tuple[str, ...] | None) -> HostReport:
    """Execute exact available assertions and return command-backed case scope."""
    run_id = uuid4().hex
    report_path = f"evidence/host/runs/{run_id}/report.json"
    defined = {case.id for case in registry().cases}
    identifiers = tuple(dict.fromkeys(requested or ()))
    unknown = tuple(case_id for case_id in identifiers if case_id not in defined)
    if unknown:
        return _publish(
            HostReport(
                run_id=run_id,
                observed_at=now(),
                requested_ids=identifiers,
                mode="host",
                exit_code=2,
                status="waiting_condition",
                unknown=unknown,
                report=report_path,
            )
        )
    prepared = prepare_host()
    if prepared.exit_code:
        return _publish(
            HostReport(
                run_id=run_id,
                observed_at=now(),
                requested_ids=identifiers,
                mode="host",
                exit_code=prepared.exit_code,
                status="failed",
                records=prepared.records,
                report=report_path,
            )
        )
    try:
        inventory = Inventory.model_validate_json(prepared.inventory)
        if requested is None:
            identifiers = tuple(
                sorted(
                    {
                        case
                        for test in inventory.tests
                        for case in test.case_ids()
                        if case in defined
                    }
                )
            )
            selection = Selection(tests=inventory.tests)
        else:
            selection = select_tests(prepared.inventory, identifiers)
    except ValidationError:
        return _publish(
            HostReport(
                run_id=run_id,
                observed_at=now(),
                requested_ids=identifiers,
                mode="host",
                exit_code=1,
                status="failed",
                records=prepared.records,
                report=report_path,
            )
        )
    if not selection.tests:
        return _publish(
            HostReport(
                run_id=run_id,
                observed_at=now(),
                requested_ids=identifiers,
                mode="host",
                exit_code=2,
                status="waiting_condition",
                unmapped=selection.unmapped,
                records=prepared.records,
                report=report_path,
            )
        )
    junit = ROOT / f"evidence/host/runs/{run_id}/junit.xml"
    junit.parent.mkdir(parents=True, exist_ok=True)
    expression = "^(" + "|".join(re.escape(test.name) for test in selection.tests) + ")$"
    environment = dict(os.environ)
    record = run(
        [
            executable("ctest"),
            "--test-dir",
            str(ROOT / "build/host"),
            "-R",
            expression,
            "--no-tests=error",
            "--output-on-failure",
            "--output-junit",
            str(junit),
        ],
        "host",
        env=environment,
    )
    outcomes = ()
    try:
        outcomes = read_junit(junit) if junit.is_file() else ()
    except ET.ParseError:
        outcomes = ()
    observed = {outcome.name: outcome for outcome in outcomes}
    tests = tuple(
        ExecutedTest(
            name=test.name,
            command=test.command,
            case_ids=test.case_ids(),
            executable_sha256=digest(Path(test.command[0]))
            if test.command and Path(test.command[0]).is_file()
            else "",
            outcome=observed[test.name],
        )
        for test in selection.tests
        if test.name in observed
    )
    complete = len(tests) == len(selection.tests) and all(
        test.outcome.result == "passed" for test in tests
    )
    code = max(record.exit_code, int(not complete), 2 if selection.unmapped else 0)
    status = (
        "failed"
        if record.exit_code or not complete
        else "waiting_condition"
        if selection.unmapped
        else "executed"
    )
    report = HostReport(
        run_id=run_id,
        observed_at=now(),
        requested_ids=identifiers,
        mode="host",
        exit_code=code,
        status=status,
        tests=tests,
        unmapped=selection.unmapped,
        records=(*prepared.records, record),
        junit=str(junit.relative_to(ROOT)),
        report=report_path,
    )
    return _publish(report)
