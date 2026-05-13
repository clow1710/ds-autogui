from __future__ import annotations

import random
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Deque

from PySide6.QtCore import QSettings, QThread, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .account import AccountPane
from .config import (
    CHAT_MODE_MODEL_TYPES,
    DEEPTHINK_TERMS,
    DEFAULT_CHAT_URL,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_PROMPTS_DIR,
    DEFAULT_RUNTIME_DIR,
    NEW_CHAT_TERMS,
    PROJECT_DIR,
    SEARCH_TERMS,
)
from .models import PromptTask, ResultPayload, ResultWriteResult, TaskLoadResult, TaskRow
from .runner import AccountRunner
from .workers import ResultWriteWorker, TaskLoadWorker


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("DeepSeek 网页批量任务")
        self.resize(1400, 900)
        self.settings = QSettings("codex-local", "deepseek-batch-gui")
        self.tasks: Deque[PromptTask] = deque()
        self.task_rows: dict[str, int] = {}
        self.accounts: list[AccountPane] = []
        self.runners: list[AccountRunner] = []
        self.task_load_thread: QThread | None = None
        self.task_load_worker: TaskLoadWorker | None = None
        self.result_threads: list[QThread] = []
        self.result_workers: list[ResultWriteWorker] = []
        self.start_requested_after_load = False

        self._build_ui()
        self._load_settings()
        self.rebuild_accounts()

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)

        self.chat_url_edit = QLineEdit(DEFAULT_CHAT_URL, self)
        self.prompts_dir_edit = QLineEdit(str(DEFAULT_PROMPTS_DIR), self)
        self.output_dir_edit = QLineEdit(str(DEFAULT_OUTPUT_DIR), self)
        self.runtime_dir_edit = QLineEdit(str(DEFAULT_RUNTIME_DIR), self)

        self.prompt_browse_button = QPushButton("选择", self)
        self.output_browse_button = QPushButton("选择", self)
        self.runtime_browse_button = QPushButton("选择", self)

        self.account_count_spin = QSpinBox(self)
        self.account_count_spin.setRange(1, 2_147_483_647)
        self.account_count_spin.setValue(1)

        self.min_delay_spin = QSpinBox(self)
        self.min_delay_spin.setRange(1, 600)
        self.min_delay_spin.setValue(1)
        self.min_delay_spin.setSuffix(" 秒")
        self.max_delay_spin = QSpinBox(self)
        self.max_delay_spin.setRange(1, 600)
        self.max_delay_spin.setValue(10)
        self.max_delay_spin.setSuffix(" 秒")

        self.poll_interval_spin = QSpinBox(self)
        self.poll_interval_spin.setRange(1, 30)
        self.poll_interval_spin.setValue(2)
        self.poll_interval_spin.setSuffix(" 秒")
        self.stable_seconds_spin = QSpinBox(self)
        self.stable_seconds_spin.setRange(2, 120)
        self.stable_seconds_spin.setValue(8)
        self.stable_seconds_spin.setSuffix(" 秒")
        self.reply_timeout_spin = QSpinBox(self)
        self.reply_timeout_spin.setRange(30, 7200)
        self.reply_timeout_spin.setValue(900)
        self.reply_timeout_spin.setSuffix(" 秒")

        self.chat_mode_combo = QComboBox(self)
        self.chat_mode_combo.addItem("专家模式", "expert")
        self.chat_mode_combo.addItem("快速模式", "fast")
        self.chat_mode_combo.addItem("保持当前页面模式", "keep")

        self.search_check = QCheckBox("启用智能搜索（网页搜索）", self)
        self.search_check.setChecked(True)
        self.require_search_check = QCheckBox("找不到网页搜索按钮时暂停账号", self)
        self.require_search_check.setChecked(True)
        self.deepthink_check = QCheckBox("启用深度思考", self)
        self.new_chat_check = QCheckBox("每个任务前尝试新对话", self)
        self.new_chat_check.setChecked(True)

        self.apply_accounts_button = QPushButton("应用账号数", self)
        self.reload_all_button = QPushButton("全部打开 DeepSeek", self)
        self.load_tasks_button = QPushButton("加载任务", self)
        self.start_button = QPushButton("开始", self)
        self.stop_button = QPushButton("停止", self)
        self.stop_button.setEnabled(False)

        path_grid = QGridLayout()
        path_grid.addWidget(QLabel("DeepSeek 地址", self), 0, 0)
        path_grid.addWidget(self.chat_url_edit, 0, 1, 1, 2)
        path_grid.addWidget(QLabel("Prompt 目录", self), 1, 0)
        path_grid.addWidget(self.prompts_dir_edit, 1, 1)
        path_grid.addWidget(self.prompt_browse_button, 1, 2)
        path_grid.addWidget(QLabel("输出目录", self), 2, 0)
        path_grid.addWidget(self.output_dir_edit, 2, 1)
        path_grid.addWidget(self.output_browse_button, 2, 2)
        path_grid.addWidget(QLabel("运行时目录", self), 3, 0)
        path_grid.addWidget(self.runtime_dir_edit, 3, 1)
        path_grid.addWidget(self.runtime_browse_button, 3, 2)

        settings_form = QFormLayout()
        settings_form.addRow("账号数", self.account_count_spin)
        settings_form.addRow("最小随机间隔", self.min_delay_spin)
        settings_form.addRow("最大随机间隔", self.max_delay_spin)
        settings_form.addRow("轮询间隔", self.poll_interval_spin)
        settings_form.addRow("回复稳定判定", self.stable_seconds_spin)
        settings_form.addRow("回复超时", self.reply_timeout_spin)
        settings_form.addRow("对话模式", self.chat_mode_combo)

        mode_layout = QVBoxLayout()
        mode_layout.addWidget(self.search_check)
        mode_layout.addWidget(self.require_search_check)
        mode_layout.addWidget(self.deepthink_check)
        mode_layout.addWidget(self.new_chat_check)

        control_layout = QHBoxLayout()
        control_layout.addWidget(self.apply_accounts_button)
        control_layout.addWidget(self.reload_all_button)
        control_layout.addStretch(1)
        control_layout.addWidget(self.load_tasks_button)
        control_layout.addWidget(self.start_button)
        control_layout.addWidget(self.stop_button)

        config_box = QGroupBox("配置", self)
        config_layout = QVBoxLayout(config_box)
        config_layout.addLayout(path_grid)
        config_layout.addLayout(settings_form)
        config_layout.addLayout(mode_layout)
        config_layout.addLayout(control_layout)

        self.task_table = QTableWidget(0, 4, self)
        self.task_table.setHorizontalHeaderLabels(["任务 ID", "状态", "账号", "Prompt 文件"])
        self.task_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.task_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.task_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.task_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)

        self.log_edit = QPlainTextEdit(self)
        self.log_edit.setReadOnly(True)
        self.log_edit.setMaximumBlockCount(2000)

        left_panel = QWidget(self)
        left_layout = QVBoxLayout(left_panel)
        left_layout.addWidget(config_box)
        left_layout.addWidget(QLabel("任务", self))
        left_layout.addWidget(self.task_table, 2)
        left_layout.addWidget(QLabel("日志", self))
        left_layout.addWidget(self.log_edit, 1)

        self.account_tabs = QTabWidget(self)

        splitter = QSplitter(self)
        splitter.addWidget(left_panel)
        splitter.addWidget(self.account_tabs)
        splitter.setSizes([430, 970])

        main_layout = QVBoxLayout(central)
        main_layout.addWidget(splitter, 1)

        self.prompt_browse_button.clicked.connect(lambda: self._choose_dir(self.prompts_dir_edit))
        self.output_browse_button.clicked.connect(lambda: self._choose_dir(self.output_dir_edit))
        self.runtime_browse_button.clicked.connect(lambda: self._choose_dir(self.runtime_dir_edit))
        self.apply_accounts_button.clicked.connect(self.rebuild_accounts)
        self.reload_all_button.clicked.connect(self.reload_all_accounts)
        self.load_tasks_button.clicked.connect(self.load_tasks)
        self.start_button.clicked.connect(self.start_batch)
        self.stop_button.clicked.connect(self.stop_batch)
        self.min_delay_spin.valueChanged.connect(self._normalize_delay_bounds)
        self.max_delay_spin.valueChanged.connect(self._normalize_delay_bounds)

    def _load_settings(self) -> None:
        self.chat_url_edit.setText(str(self.settings.value("chat_url", DEFAULT_CHAT_URL)))
        self.prompts_dir_edit.setText(str(self.settings.value("prompts_dir", DEFAULT_PROMPTS_DIR)))
        self.output_dir_edit.setText(str(self.settings.value("output_dir", DEFAULT_OUTPUT_DIR)))
        self.runtime_dir_edit.setText(str(self.settings.value("runtime_dir", DEFAULT_RUNTIME_DIR)))
        self.account_count_spin.setValue(int(self.settings.value("account_count", 1)))
        self.min_delay_spin.setValue(int(self.settings.value("min_delay", 1)))
        self.max_delay_spin.setValue(int(self.settings.value("max_delay", 10)))
        self.poll_interval_spin.setValue(int(self.settings.value("poll_interval", 2)))
        self.stable_seconds_spin.setValue(int(self.settings.value("stable_seconds", 8)))
        self.reply_timeout_spin.setValue(int(self.settings.value("reply_timeout", 900)))
        chat_mode = str(self.settings.value("chat_mode", "expert"))
        chat_mode_index = self.chat_mode_combo.findData(chat_mode)
        if chat_mode_index < 0:
            chat_mode_index = 0
        self.chat_mode_combo.setCurrentIndex(chat_mode_index)
        self.search_check.setChecked(str(self.settings.value("search_enabled", "true")).lower() == "true")
        self.require_search_check.setChecked(str(self.settings.value("require_search", "true")).lower() == "true")
        self.deepthink_check.setChecked(str(self.settings.value("deepthink_enabled", "false")).lower() == "true")
        self.new_chat_check.setChecked(str(self.settings.value("new_chat", "true")).lower() == "true")

    def _save_settings(self) -> None:
        self.settings.setValue("chat_url", self.chat_url_edit.text().strip())
        self.settings.setValue("prompts_dir", self.prompts_dir_edit.text().strip())
        self.settings.setValue("output_dir", self.output_dir_edit.text().strip())
        self.settings.setValue("runtime_dir", self.runtime_dir_edit.text().strip())
        self.settings.setValue("account_count", self.account_count_spin.value())
        self.settings.setValue("min_delay", self.min_delay_spin.value())
        self.settings.setValue("max_delay", self.max_delay_spin.value())
        self.settings.setValue("poll_interval", self.poll_interval_spin.value())
        self.settings.setValue("stable_seconds", self.stable_seconds_spin.value())
        self.settings.setValue("reply_timeout", self.reply_timeout_spin.value())
        self.settings.setValue("chat_mode", self.chat_mode_combo.currentData() or "expert")
        self.settings.setValue("search_enabled", self.search_check.isChecked())
        self.settings.setValue("require_search", self.require_search_check.isChecked())
        self.settings.setValue("deepthink_enabled", self.deepthink_check.isChecked())
        self.settings.setValue("new_chat", self.new_chat_check.isChecked())
        self.settings.sync()

    def _choose_dir(self, target: QLineEdit) -> None:
        start = target.text().strip() or str(PROJECT_DIR)
        directory = QFileDialog.getExistingDirectory(self, "选择目录", start)
        if directory:
            target.setText(directory)

    def _normalize_delay_bounds(self) -> None:
        if self.min_delay_spin.value() > self.max_delay_spin.value():
            self.max_delay_spin.setValue(self.min_delay_spin.value())

    def log(self, message: str) -> None:
        self.log_edit.appendPlainText(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

    def runtime_dir(self) -> Path:
        return Path(self.runtime_dir_edit.text()).expanduser().resolve()

    def prompts_dir(self) -> Path:
        return Path(self.prompts_dir_edit.text()).expanduser().resolve()

    def output_dir(self) -> Path:
        return Path(self.output_dir_edit.text()).expanduser().resolve()

    def output_path_for(self, task: PromptTask) -> Path:
        return self.output_dir() / f"{task.task_id}.json"

    def automation_options(self) -> dict[str, Any]:
        return {
            "chatMode": self.chat_mode_combo.currentData() or "expert",
            "chatModeModelTypes": CHAT_MODE_MODEL_TYPES,
            "enableSearch": self.search_check.isChecked(),
            "requireSearch": self.require_search_check.isChecked(),
            "searchTerms": SEARCH_TERMS,
            "enableDeepThink": self.deepthink_check.isChecked(),
            "requireDeepThink": False,
            "deepThinkTerms": DEEPTHINK_TERMS,
            "newChatTerms": NEW_CHAT_TERMS,
        }

    def random_delay_ms(self) -> int:
        low = self.min_delay_spin.value()
        high = self.max_delay_spin.value()
        return int(random.uniform(low, high) * 1000)

    def rebuild_accounts(self) -> None:
        if any(runner.running for runner in self.runners):
            QMessageBox.warning(self, "正在运行", "请先停止任务，再调整账号数。")
            return

        self._save_settings()
        self._dispose_runners()
        self._dispose_accounts()
        self.accounts.clear()

        runtime_dir = self.runtime_dir()
        runtime_dir.mkdir(parents=True, exist_ok=True)
        chat_url = self.chat_url_edit.text().strip() or DEFAULT_CHAT_URL
        for account_id in range(1, self.account_count_spin.value() + 1):
            account = AccountPane(account_id, runtime_dir, chat_url, self)
            runner = AccountRunner(account, self)
            runner.status_changed.connect(self._runner_status_changed)
            self.accounts.append(account)
            self.runners.append(runner)
            self.account_tabs.addTab(account, f"账号 {account_id}")
        self.log(f"已加载 {len(self.accounts)} 个账号视图。首次使用请在各标签页手动登录。")

    def _dispose_runners(self) -> None:
        for runner in self.runners:
            runner.stop()
            runner.deleteLater()
        self.runners.clear()

    def _dispose_accounts(self) -> None:
        self.account_tabs.clear()
        for account in self.accounts:
            account.dispose()
            account.setParent(None)
            account.deleteLater()

    def reload_all_accounts(self) -> None:
        self._save_settings()
        chat_url = self.chat_url_edit.text().strip() or DEFAULT_CHAT_URL
        for account in self.accounts:
            account.chat_url = chat_url
            account.load_chat()
        self.log("已打开 DeepSeek 登录/聊天页面。")

    def load_tasks(self) -> None:
        if self.task_load_thread is not None:
            return

        self._save_settings()
        self.tasks.clear()
        self.task_rows.clear()
        self.task_table.setRowCount(0)
        self.load_tasks_button.setEnabled(False)
        self.log("正在后台扫描 prompt 目录。")

        thread = QThread(self)
        worker = TaskLoadWorker(self.prompts_dir(), self.output_dir())
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._tasks_loaded)
        worker.failed.connect(self._tasks_load_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._task_loader_finished)
        self.task_load_thread = thread
        self.task_load_worker = worker
        thread.start()

    def _tasks_loaded(self, result: TaskLoadResult) -> None:
        self.tasks = deque(result.tasks)
        for row in result.rows:
            self._add_task_row(row)
        self.log(
            f"已加载 {len(result.tasks)} 个待处理任务；"
            f"已跳过完成 {result.skipped_done} 个，空 prompt {result.skipped_empty} 个，读取失败 {result.skipped_error} 个。"
        )
        if self.start_requested_after_load:
            self.start_requested_after_load = False
            if self.tasks:
                QTimer.singleShot(0, self.start_batch)
            else:
                QMessageBox.information(self, "没有任务", "未找到可处理的 .prompt 文件。")

    def _tasks_load_failed(self, error: str) -> None:
        self.start_requested_after_load = False
        QMessageBox.warning(self, "加载失败", error)
        self.log(f"任务加载失败：{error}")

    def _task_loader_finished(self) -> None:
        self.task_load_thread = None
        self.task_load_worker = None
        self.load_tasks_button.setEnabled(True)

    def _add_task_row(self, row_data: TaskRow) -> None:
        row = self.task_table.rowCount()
        self.task_table.insertRow(row)
        self.task_table.setItem(row, 0, QTableWidgetItem(row_data.task_id))
        self.task_table.setItem(row, 1, QTableWidgetItem(row_data.status))
        self.task_table.setItem(row, 2, QTableWidgetItem(row_data.account))
        self.task_table.setItem(row, 3, QTableWidgetItem(str(row_data.prompt_path)))
        self.task_rows[row_data.task_id] = row

    def mark_task(self, task_id: str, status: str, account_id: int | str = "") -> None:
        row = self.task_rows.get(task_id)
        if row is None:
            return
        status_item = self.task_table.item(row, 1)
        account_item = self.task_table.item(row, 2)
        if status_item is not None:
            status_item.setText(status)
        if account_item is not None:
            account_item.setText(str(account_id))

    def take_next_task(self) -> PromptTask | None:
        if not self.tasks:
            return None
        return self.tasks.popleft()

    def requeue_task(self, task: PromptTask) -> None:
        self.tasks.appendleft(task)

    def write_result_async(
        self,
        payload: ResultPayload,
        on_done: Callable[[ResultWriteResult], None],
        on_failed: Callable[[str, str], None],
    ) -> None:
        thread = QThread(self)
        worker = ResultWriteWorker(payload)
        worker.moveToThread(thread)

        worker.finished.connect(on_done)
        worker.failed.connect(on_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.started.connect(worker.run)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._cleanup_result_worker(thread, worker))

        self.result_threads.append(thread)
        self.result_workers.append(worker)
        thread.start()

    def _cleanup_result_worker(self, thread: QThread, worker: ResultWriteWorker) -> None:
        if thread in self.result_threads:
            self.result_threads.remove(thread)
        if worker in self.result_workers:
            self.result_workers.remove(worker)

    def start_batch(self) -> None:
        if self.task_load_thread is not None:
            self.start_requested_after_load = True
            self.log("任务仍在后台加载，加载完成后会自动开始。")
            return

        if not self.tasks:
            self.start_requested_after_load = True
            self.load_tasks()
            return

        self._save_settings()
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.load_tasks_button.setEnabled(False)
        self.apply_accounts_button.setEnabled(False)

        cumulative_delay = 0
        for index, runner in enumerate(self.runners):
            if index > 0:
                cumulative_delay += self.random_delay_ms()
            runner.start_after(cumulative_delay)
        self.log("批处理已启动。遇到验证码、风控或登录过期时，请在对应账号标签页手动处理后重新开始。")

    def stop_batch(self) -> None:
        for runner in self.runners:
            runner.stop()
        self._batch_finished()
        self.log("已请求停止。已发送到网页的任务不会自动撤回。")

    def runner_idle(self, runner: AccountRunner) -> None:
        if any(item.running for item in self.runners):
            return
        self._batch_finished()
        if self.tasks:
            self.log(f"批处理暂停，仍有 {len(self.tasks)} 个任务待处理。")
        else:
            self.log("批处理完成。")

    def _batch_finished(self) -> None:
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.load_tasks_button.setEnabled(True)
        self.apply_accounts_button.setEnabled(True)

    def _runner_status_changed(self, account_id: int, status: str) -> None:
        index = account_id - 1
        if 0 <= index < self.account_tabs.count():
            self.account_tabs.setTabText(index, f"账号 {account_id} - {status}")

    def closeEvent(self, event: Any) -> None:
        self._save_settings()
        for runner in self.runners:
            runner.stop()
        if self.task_load_thread is not None:
            self.task_load_thread.quit()
            self.task_load_thread.wait(1500)
        for thread in list(self.result_threads):
            thread.quit()
            thread.wait(1500)
        self._dispose_runners()
        self._dispose_accounts()
        super().closeEvent(event)
