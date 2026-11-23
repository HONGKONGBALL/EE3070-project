"""Task status and recurring checkpoints from actual execution records."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Literal

from project import H0, ROOT, Model, RunRecord, now, save
from pydantic import Field, JsonValue, TypeAdapter

if TYPE_CHECKING:
    from pathlib import Path

Status = Literal["planned", "running", "waiting_condition", "awaiting_review", "accepted"]


class TaskProgress(Model):
    """Current task state with append-only command evidence references."""

    task_id: str
    status: Status = "planned"
    started_at: str | None = None
    finished_at: str | None = None
    completion: int = Field(default=0, ge=0, le=100)
    commands: tuple[str, ...] = ()
    exit_codes: tuple[int, ...] = ()
    artifacts: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    acceptance: str = "pending"
    next_action: str = "按任务输入推进"
    updated_at: str = ""


class Progress(Model):
    """Persisted execution start and all task transitions."""

    schema_version: int = 1
    h0: str = H0
    updated_at: str
    tasks: tuple[TaskProgress, ...]


def load_progress() -> Progress:
    """Load execution progress or construct its first scheduled state."""
    path = ROOT / "progress.json"
    if path.is_file():
        return Progress.model_validate_json(path.read_text())
    return Progress(
        updated_at=now(),
        tasks=tuple(TaskProgress(task_id=f"T{index:02}") for index in range(1, 25)),
    )


def update(
    task_id: str,
    status: Status,
    completion: int,
    records: tuple[RunRecord, ...],
    artifacts: tuple[str, ...],
    next_action: str,
    acceptance: str = "pending",
) -> None:
    """Append evidence and persist a single task transition."""
    progress = load_progress()
    tasks: list[TaskProgress] = []
    for task in progress.tasks:
        updated_task = task
        if task.task_id == task_id:
            updated_task = task.model_copy(
                update={
                    "status": status,
                    "completion": completion,
                    "started_at": task.started_at or now(),
                    "finished_at": now() if status == "accepted" else None,
                    "commands": (*task.commands, *(" ".join(record.command) for record in records)),
                    "exit_codes": (*task.exit_codes, *(record.exit_code for record in records)),
                    "artifacts": tuple(dict.fromkeys((*task.artifacts, *artifacts))),
                    "evidence": (*task.evidence, *(record.log for record in records)),
                    "next_action": next_action,
                    "acceptance": acceptance,
                    "updated_at": now(),
                }
            )
        tasks.append(updated_task)
    save(ROOT / "progress.json", Progress(updated_at=now(), tasks=tuple(tasks)))
    checkpoint()


def checkpoint() -> None:
    """Capture the current task entry points and schedule."""
    progress = load_progress()
    deadline = datetime.fromisoformat(progress.h0) + timedelta(hours=24)
    lines = [
        "# 导航模块 Checkpoint",
        "",
        f"更新：{now()}",
        f"H0：{progress.h0}",
        f"H24：{deadline.isoformat()}",
        "",
        "| 任务 | 状态 | 进度 | 下一步 |",
        "| --- | --- | --- | --- |",
    ]
    lines.extend(
        f"| {task.task_id} | {task.status} | {task.completion}% | {task.next_action} |"
        for task in progress.tasks
        if task.status != "planned"
    )
    lines.extend(
        [
            "",
            "下一条命令：",
            "",
            "```sh",
            "python3 navigation_24h/tools/navctl.py status",
            "```",
            "",
            "证据入口：`evidence/`；用例注册：`config/test_registry.json`；配置模板：`config/local.example.json`。",
        ]
    )
    _ = (ROOT / "checkpoint.md").write_text("\n".join(lines) + "\n")


def read_json(path: Path) -> JsonValue:
    """Read auxiliary catalog JSON at a serialization boundary."""
    return TypeAdapter(JsonValue).validate_json(path.read_text())
