from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QTimer, Signal

from .account import AccountPane
from .js_bridge import js_call
from .models import CheckHookResult, PromptTask, ResultPayload, ResultWriteResult
from .utils import now_iso

if TYPE_CHECKING:
    from .window import MainWindow


class AccountRunner(QObject):
    status_changed = Signal(int, str)
    stats_updated = Signal(int)

    def __init__(self, account: AccountPane, window: "MainWindow") -> None:
        super().__init__(window)
        self.account = account
        self.window = window
        self.running = False
        self.current_task: PromptTask | None = None
        self.request_time = ""
        self.last_content = ""
        self.last_change_monotonic = 0.0
        self.poll_started_monotonic = 0.0
        self.task_start_monotonic = 0.0
        self.assigned_count = 0
        self.success_count = 0
        self.failure_count = 0
        self.total_success_seconds = 0.0
        self.pending_hook_output_path: Path | None = None
        self.next_action_deadline_monotonic = 0.0
        self.baseline_bubble_count: int | None = None
        self._approaching_timeout_focused = False
        self.busy_rounds = 0

    @property
    def avg_success_seconds(self) -> float:
        if self.success_count == 0:
            return 0.0
        return self.total_success_seconds / self.success_count

    @property
    def remaining_wait_seconds(self) -> float:
        if not self.running or self.current_task is not None:
            return 0.0
        remaining = self.next_action_deadline_monotonic - time.monotonic()
        return max(0.0, remaining)

    @property
    def current_reply_wait_seconds(self) -> float:
        if self.current_task is None or self.poll_started_monotonic <= 0:
            return 0.0
        return max(0.0, time.monotonic() - self.poll_started_monotonic)

    def _schedule_next_task(self, delay_ms: int) -> None:
        self.next_action_deadline_monotonic = time.monotonic() + delay_ms / 1000
        QTimer.singleShot(delay_ms, self._take_next_task)

    def _handle_busy_round(self, task: PromptTask, reason: str) -> None:
        # 繁忙重试期间不暂停账号：把任务塞回队首，进入新一轮冷却等待。
        self.busy_rounds += 1
        cooldown_ms = self.window.busy_cooldown_ms()
        round_label = f"第{self.busy_rounds}轮"
        self.window.requeue_task(task, self.account.account_id)
        self.window.mark_task(
            task.task_id,
            f"{reason}（{round_label}），{cooldown_ms / 1000:.0f}s 后重试",
            self.account.account_id,
        )
        self.window.log(
            f"账号 {self.account.account_id} 任务 {task.task_id} {reason}，"
            f"{round_label}繁忙等待 {cooldown_ms / 1000:.0f}s 后重试。"
        )
        self.current_task = None
        self.task_start_monotonic = 0.0
        self.last_content = ""
        self.poll_started_monotonic = 0.0
        self.status_changed.emit(
            self.account.account_id,
            f"服务器繁忙 {round_label} {cooldown_ms / 1000:.0f}s 冷却",
        )
        self._schedule_next_task(cooldown_ms)

    def _record_assigned(self) -> None:
        self.assigned_count += 1
        self.stats_updated.emit(self.account.account_id)

    def _record_success(self) -> None:
        if self.task_start_monotonic > 0:
            self.total_success_seconds += time.monotonic() - self.task_start_monotonic
        self.success_count += 1
        self.task_start_monotonic = 0.0
        self.busy_rounds = 0
        self.stats_updated.emit(self.account.account_id)

    def _record_failure(self) -> None:
        self.failure_count += 1
        self.task_start_monotonic = 0.0
        self.stats_updated.emit(self.account.account_id)

    def start_after(self, delay_ms: int) -> None:
        self.running = True
        self.status_changed.emit(self.account.account_id, f"{delay_ms / 1000:.1f}s 后启动")
        self._schedule_next_task(delay_ms)

    def stop(self) -> None:
        self.running = False
        self.next_action_deadline_monotonic = 0.0
        self.status_changed.emit(self.account.account_id, "已停止")

    def _take_next_task(self) -> None:
        self.next_action_deadline_monotonic = 0.0
        if not self.running:
            return

        task = self.window.take_next_task(self.account.account_id)
        if task is None:
            self.running = False
            self.status_changed.emit(self.account.account_id, "无待处理任务")
            self.window.runner_idle(self)
            return

        self.current_task = task
        self.last_content = ""
        self.last_change_monotonic = time.monotonic()
        self.poll_started_monotonic = 0.0
        self.task_start_monotonic = time.monotonic()
        self.baseline_bubble_count = None
        self._record_assigned()
        self.window.mark_task(task.task_id, "准备发送", self.account.account_id)
        self.status_changed.emit(self.account.account_id, f"任务 {task.task_id}")

        # 切到此账号 tab 后页面才会被 Chromium 视为可见，
        # 隐藏状态下首次加载 / 新对话不会启动流式渲染管线。
        self.window.focus_account_tab(self.account.account_id)
        QTimer.singleShot(200, self._dispatch_task_action)

    def _dispatch_task_action(self) -> None:
        if not self.running or self.current_task is None:
            return
        if self.window.new_chat_check.isChecked():
            self.account.run_js(js_call("newChat", self.window.automation_options()), self._after_new_chat)
        else:
            self._prepare_and_send()

    def _after_new_chat(self, result: dict[str, Any]) -> None:
        if not self.running or self.current_task is None:
            return
        if result.get("ok"):
            self.window.log(f"账号 {self.account.account_id} 已切换新对话")
            QTimer.singleShot(1500, self._prepare_and_send)
        else:
            self.window.log(
                f"账号 {self.account.account_id} 未找到新对话按钮，改为打开 DeepSeek 首页创建空白对话："
                f"{result.get('warning') or result.get('error')}"
            )
            self._load_blank_chat_then_prepare()

    def _load_blank_chat_then_prepare(self) -> None:
        completed = {"done": False}

        def after_load(ok: bool) -> None:
            if completed["done"]:
                return
            completed["done"] = True
            try:
                self.account.page.loadFinished.disconnect(after_load)
            except (RuntimeError, TypeError):
                pass
            if not self.running or self.current_task is None:
                return
            if ok:
                QTimer.singleShot(800, self._prepare_and_send)
            else:
                QTimer.singleShot(3000, self._prepare_and_send)

        self.account.page.loadFinished.connect(after_load)
        self.window.focus_account_tab(self.account.account_id)
        self.account.load_chat()
        QTimer.singleShot(10000, lambda: after_load(False))

    def _prepare_and_send(self) -> None:
        if not self.running or self.current_task is None:
            return
        task = self.current_task
        self.window.mark_task(task.task_id, "切换模式", self.account.account_id)
        self._select_chat_mode(0)

    def _select_chat_mode(self, attempt: int) -> None:
        if not self.running or self.current_task is None:
            return
        self.account.run_js(
            js_call("selectChatMode", self.window.automation_options()),
            lambda result: self._after_select_chat_mode(result, attempt),
        )

    def _after_select_chat_mode(self, result: dict[str, Any], attempt: int) -> None:
        if not self.running or self.current_task is None:
            return

        task = self.current_task
        if result.get("ok"):
            if result.get("changed"):
                mode_name = "专家模式" if result.get("mode") == "expert" else "快速模式"
                self.window.log(f"账号 {self.account.account_id} 已切换到{mode_name}")
            QTimer.singleShot(300, self._send_current_task)
            return

        if result.get("retry") and attempt < 3:
            QTimer.singleShot(400, lambda: self._select_chat_mode(attempt + 1))
            return

        error = result.get("error") or result.get("warning") or "对话模式切换失败"
        if self.busy_rounds > 0:
            self._handle_busy_round(task, f"繁忙重试中模式切换失败：{error}")
            return
        self.running = False
        self._record_failure()
        self.window.requeue_task(task, self.account.account_id)
        self.window.mark_task(task.task_id, f"已重排：{error}", self.account.account_id)
        self.window.log(f"账号 {self.account.account_id} 未发送任务 {task.task_id}，已重排：{error}")
        self.status_changed.emit(self.account.account_id, "已暂停")
        self.current_task = None
        self.window.runner_idle(self)

    def _send_current_task(self) -> None:
        if not self.running or self.current_task is None:
            return
        task = self.current_task
        self.window.mark_task(task.task_id, "发送中", self.account.account_id)
        self.account.run_js(js_call("prepareAndSend", task.prompt, self.window.automation_options()), self._after_send)

    def _after_send(self, result: dict[str, Any]) -> None:
        if not self.running or self.current_task is None:
            return
        task = self.current_task
        if not result.get("ok"):
            send_error = result.get("error") or "发送失败"
            if self.busy_rounds > 0:
                self._handle_busy_round(task, f"繁忙重试中发送失败：{send_error}")
                return
            self.running = False
            self._record_failure()
            self.window.requeue_task(task, self.account.account_id)
            self.window.mark_task(task.task_id, f"已重排：{send_error}", self.account.account_id)
            self.window.log(f"账号 {self.account.account_id} 未发送任务 {task.task_id}，已重排：{send_error}")
            self.status_changed.emit(self.account.account_id, "已暂停")
            self.current_task = None
            self.window.runner_idle(self)
            return

        baseline = result.get("baselineBubbleCount")
        self.baseline_bubble_count = int(baseline) if isinstance(baseline, (int, float)) else None
        self.request_time = now_iso()
        self.poll_started_monotonic = time.monotonic()
        self.last_change_monotonic = self.poll_started_monotonic
        self._approaching_timeout_focused = False
        self.window.mark_task(task.task_id, "等待回复", self.account.account_id)
        self.window.log(f"账号 {self.account.account_id} 已发送任务 {task.task_id}")
        QTimer.singleShot(self.window.poll_interval_spin.value() * 1000, self._poll_reply)

    def _poll_reply(self) -> None:
        if not self.running or self.current_task is None:
            return
        task = self.current_task
        self.account.run_js(
            js_call("collectReply", task.prompt, self.baseline_bubble_count),
            self._after_poll,
        )

    def _after_poll(self, result: dict[str, Any]) -> None:
        if not self.running or self.current_task is None:
            return

        task = self.current_task
        now = time.monotonic()

        if result.get("ok") and result.get("serverBusy"):
            self._handle_busy_round(task, "服务器繁忙")
            return

        timeout = self.window.reply_timeout_spin.value()
        elapsed_wait = now - self.poll_started_monotonic
        if (
            not self._approaching_timeout_focused
            and timeout > 0
            and elapsed_wait >= timeout * 0.8
        ):
            self._approaching_timeout_focused = True
            self.window.focus_account_tab(self.account.account_id)
            self.window.log(
                f"账号 {self.account.account_id} 等待回复 {elapsed_wait:.0f}/{timeout}s，"
                f"接近超时已自动切到该标签页。"
            )
        if elapsed_wait > timeout:
            self.running = False
            self._record_failure()
            self.window.mark_task(task.task_id, "超时，账号暂停", self.account.account_id)
            self.window.log(f"账号 {self.account.account_id} 等待任务 {task.task_id} 回复超时")
            self.status_changed.emit(self.account.account_id, "已暂停")
            self.current_task = None
            self.window.runner_idle(self)
            return

        if not result.get("ok"):
            self.window.mark_task(task.task_id, f"读取回复失败：{result.get('error')}", self.account.account_id)
            QTimer.singleShot(self.window.poll_interval_spin.value() * 1000, self._poll_reply)
            return

        content = str(result.get("content") or "").strip()
        if content and content != self.last_content:
            self.last_content = content
            self.last_change_monotonic = now
            self.busy_rounds = 0
            self.window.mark_task(task.task_id, f"接收中 {len(content)} 字", self.account.account_id)

        stable_for = now - self.last_change_monotonic
        stable_enough = content and stable_for >= self.window.stable_seconds_spin.value()
        generating = bool(result.get("generating"))
        if stable_enough and not generating:
            self._finish_task(content)
            return

        QTimer.singleShot(self.window.poll_interval_spin.value() * 1000, self._poll_reply)

    def _finish_task(self, content: str) -> None:
        if self.current_task is None:
            return
        task = self.current_task
        payload = ResultPayload(
            task=task,
            account_id=self.account.account_id,
            request_time=self.request_time,
            response_time=now_iso(),
            response=content,
            output_dir=self.window.output_dir(),
        )
        self.window.mark_task(task.task_id, "写入结果", self.account.account_id)
        self.window.write_result_async(payload, self._after_result_written, self._after_result_write_failed)

    def _after_result_written(self, result: ResultWriteResult) -> None:
        if self.current_task is None:
            return
        task = self.current_task
        self.window.log(f"账号 {self.account.account_id} 写入任务 {task.task_id} -> {result.output_path}")

        if self.window.check_hook_enabled():
            self.window.mark_task(task.task_id, "校验中", self.account.account_id)
            self.status_changed.emit(self.account.account_id, f"任务 {task.task_id} 校验中")
            self.pending_hook_output_path = result.output_path
            self.window.run_check_hook_async(
                task,
                result.output_path,
                self._after_check_hook,
                self._after_check_hook_error,
            )
            return

        self.window.mark_task(task.task_id, "完成", self.account.account_id)
        self.window.reset_hook_retry(task.task_id)
        self._record_success()
        self._continue_after_task()

    def _after_check_hook(self, result: CheckHookResult) -> None:
        task = self.current_task
        output_path = self.pending_hook_output_path
        if task is None or output_path is None or task.task_id != result.task_id:
            return
        self.pending_hook_output_path = None

        if result.passed:
            self.window.mark_task(task.task_id, "完成（校验通过）", self.account.account_id)
            self.window.log(f"账号 {self.account.account_id} 任务 {task.task_id} 校验通过")
            self.window.reset_hook_retry(task.task_id)
            self._record_success()
            self._continue_after_task()
            return

        stderr_tail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else ""
        stdout_tail = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
        reason = stderr_tail or stdout_tail or f"退出码 {result.returncode}"
        self._handle_check_failure(task, output_path, reason)

    def _after_check_hook_error(self, task_id: str, error: str) -> None:
        task = self.current_task
        output_path = self.pending_hook_output_path
        if task is None or output_path is None or task.task_id != task_id:
            return
        self.pending_hook_output_path = None
        self._handle_check_failure(task, output_path, f"检查脚本异常：{error}")

    def _handle_check_failure(self, task: PromptTask, output_path: Path, reason: str) -> None:
        self._record_failure()
        attempts = self.window.bump_hook_retry(task.task_id)
        max_retries = self.window.check_hook_max_retries()
        if attempts <= max_retries:
            try:
                output_path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                self.window.log(
                    f"账号 {self.account.account_id} 校验失败但删除输出 {output_path} 失败：{exc}"
                )
            self.window.requeue_task(task, self.account.account_id)
            self.window.mark_task(
                task.task_id,
                f"校验失败 第{attempts}/{max_retries}次 已重排：{reason}",
                self.account.account_id,
            )
            self.window.log(
                f"账号 {self.account.account_id} 任务 {task.task_id} 第 {attempts} 次校验失败，已重排：{reason}"
            )
        else:
            self.window.mark_task(
                task.task_id,
                f"校验失败 达上限({max_retries})：{reason}",
                self.account.account_id,
            )
            self.window.log(
                f"账号 {self.account.account_id} 任务 {task.task_id} 校验失败达上限 {max_retries}，已保留输出：{reason}"
            )
            self.window.reset_hook_retry(task.task_id)
        self._continue_after_task()

    def _continue_after_task(self) -> None:
        self.current_task = None
        if not self.running:
            self.window.runner_idle(self)
            return
        delay_ms = self.window.random_delay_ms()
        self.status_changed.emit(self.account.account_id, f"{delay_ms / 1000:.1f}s 后继续")
        self._schedule_next_task(delay_ms)

    def _after_result_write_failed(self, task_id: str, error: str) -> None:
        self.running = False
        self._record_failure()
        if self.current_task is not None and self.current_task.task_id == task_id:
            self.window.requeue_task(self.current_task, self.account.account_id)
            status = f"写入失败已重排：{error}"
            log_message = f"账号 {self.account.account_id} 写入任务 {task_id} 失败，已重排：{error}"
        else:
            status = f"写入失败：{error}"
            log_message = f"账号 {self.account.account_id} 写入任务 {task_id} 失败：{error}"
        self.window.mark_task(task_id, status, self.account.account_id)
        self.window.log(log_message)
        self.status_changed.emit(self.account.account_id, "已暂停")
        self.current_task = None
        self.window.runner_idle(self)
