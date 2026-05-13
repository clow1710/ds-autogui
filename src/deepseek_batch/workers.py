from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from .models import (
    CheckHookResult,
    PromptTask,
    ResultPayload,
    ResultWriteResult,
    TaskLoadResult,
    TaskRow,
)


class TaskLoadWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, prompt_dir: Path, output_dir: Path) -> None:
        super().__init__()
        self.prompt_dir = prompt_dir
        self.output_dir = output_dir

    @Slot()
    def run(self) -> None:
        try:
            self.prompt_dir.mkdir(parents=True, exist_ok=True)
            self.output_dir.mkdir(parents=True, exist_ok=True)

            tasks: list[PromptTask] = []
            rows: list[TaskRow] = []
            skipped_done = 0
            skipped_empty = 0
            skipped_error = 0

            for path in sorted(self.prompt_dir.glob("*.prompt")):
                task_id = path.stem
                if (self.output_dir / f"{task_id}.json").exists():
                    skipped_done += 1
                    rows.append(TaskRow(task_id, "已存在输出，跳过", "", path))
                    continue

                try:
                    prompt = path.read_text(encoding="utf-8-sig").strip()
                except UnicodeDecodeError:
                    prompt = path.read_text(encoding="utf-8").strip()
                except OSError as exc:
                    skipped_error += 1
                    rows.append(TaskRow(task_id, f"读取失败：{exc}", "", path))
                    continue

                if not prompt:
                    skipped_empty += 1
                    rows.append(TaskRow(task_id, "空文件，跳过", "", path))
                    continue

                task = PromptTask(task_id=task_id, prompt_path=path, prompt=prompt)
                tasks.append(task)
                rows.append(TaskRow(task_id, "待处理", "", path))

            self.finished.emit(TaskLoadResult(tasks, rows, skipped_done, skipped_empty, skipped_error))
        except Exception as exc:  # noqa: BLE001 - worker boundary reports failures to GUI.
            self.failed.emit(str(exc))


class ResultWriteWorker(QObject):
    finished = Signal(object)
    failed = Signal(str, str)

    def __init__(self, payload: ResultPayload) -> None:
        super().__init__()
        self.payload = payload

    @Slot()
    def run(self) -> None:
        task = self.payload.task
        output_path = self.payload.output_dir / f"{task.task_id}.json"
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "task_id": task.task_id,
                "account_id": self.payload.account_id,
                "request_time": self.payload.request_time,
                "response_time": self.payload.response_time,
                "prompt": task.prompt,
                "response": self.payload.response,
            }
            temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
            temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(output_path)
            self.finished.emit(ResultWriteResult(task.task_id, output_path))
        except Exception as exc:  # noqa: BLE001 - worker boundary reports failures to GUI.
            self.failed.emit(task.task_id, str(exc))


class CheckHookWorker(QObject):
    finished = Signal(object)
    failed = Signal(str, str)

    def __init__(
        self,
        task_id: str,
        hook_path: Path,
        output_path: Path,
        prompt_path: Path,
        timeout_seconds: int = 180,
    ) -> None:
        super().__init__()
        self.task_id = task_id
        self.hook_path = hook_path
        self.output_path = output_path
        self.prompt_path = prompt_path
        self.timeout_seconds = timeout_seconds

    @Slot()
    def run(self) -> None:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    str(self.hook_path),
                    str(self.output_path),
                    str(self.prompt_path),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                check=False,
                env=env,
            )
            self.finished.emit(
                CheckHookResult(
                    task_id=self.task_id,
                    output_path=self.output_path,
                    returncode=proc.returncode,
                    stdout=proc.stdout or "",
                    stderr=proc.stderr or "",
                )
            )
        except subprocess.TimeoutExpired:
            self.failed.emit(self.task_id, f"检查脚本超时（>{self.timeout_seconds}s）")
        except Exception as exc:  # noqa: BLE001 - worker boundary reports failures to GUI.
            self.failed.emit(self.task_id, str(exc))

