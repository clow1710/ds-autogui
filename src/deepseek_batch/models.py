from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PromptTask:
    task_id: str
    prompt_path: Path
    prompt: str


@dataclass(frozen=True)
class TaskRow:
    task_id: str
    status: str
    account: str
    prompt_path: Path


@dataclass(frozen=True)
class TaskLoadResult:
    tasks: list[PromptTask]
    rows: list[TaskRow]
    skipped_done: int
    skipped_empty: int
    skipped_error: int


@dataclass(frozen=True)
class ResultPayload:
    task: PromptTask
    account_id: int
    request_time: str
    response_time: str
    response: str
    output_dir: Path


@dataclass(frozen=True)
class ResultWriteResult:
    task_id: str
    output_path: Path

