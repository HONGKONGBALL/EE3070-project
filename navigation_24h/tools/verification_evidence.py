"""Acceptance observations derived from real host assertion evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from acceptance import CaseResult, has_evidence, persist
from project import ROOT, Model, save
from verification_types import ExecutedTest  # noqa: TC002 - Pydantic resolves field models at runtime.

if TYPE_CHECKING:
    from verification_types import HostReport


class CaseObservation(Model):
    """Host component assertions attached to one source acceptance case."""

    case_id: str
    result: Literal["partial", "failed"]
    observed_at: str
    tests: tuple[ExecutedTest, ...]
    evidence: tuple[str, ...]
    preserved_complete_result: bool


def retain_case_observations(report: HostReport) -> None:
    """Record host components while retaining completed device acceptance evidence."""
    for case_id in report.requested_ids:
        tests = tuple(test for test in report.tests if case_id in test.case_ids)
        if not tests:
            continue
        path = ROOT / "evidence" / case_id / "result.json"
        previous = CaseResult.model_validate_json(path.read_text()) if path.is_file() else None
        preserved = bool(
            previous
            and previous.result == "passed"
            and previous.mode in {"device", "mixed"}
            and has_evidence(previous)
        )
        evidence = (report.report, report.junit, *(record.log for record in report.records))
        failed = any(test.outcome.result == "failed" for test in tests)
        observation = CaseObservation(
            case_id=case_id,
            result="failed" if failed else "partial",
            observed_at=report.observed_at,
            tests=tests,
            evidence=evidence,
            preserved_complete_result=preserved,
        )
        save(ROOT / "evidence" / case_id / "host" / f"{report.run_id}.json", observation)
        if not preserved:
            persist(
                CaseResult(
                    case_id=case_id,
                    result="failed" if failed else "partial",
                    mode="host",
                    evidence=evidence,
                    observation="Host component assertions: "
                    + ", ".join(test.name for test in tests),
                )
            )
