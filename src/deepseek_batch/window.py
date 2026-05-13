from __future__ import annotations

import json
import random
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Deque

from PySide6.QtCore import Qt, QSettings, QThread, QTimer
from PySide6.QtGui import QFontDatabase, QFontMetrics
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
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
from .js_bridge import js_call
from .models import (
    CheckHookResult,
    PromptTask,
    ResultPayload,
    ResultWriteResult,
    TaskLoadResult,
    TaskRow,
)
from .runner import AccountRunner
from .workers import CheckHookWorker, ResultWriteWorker, TaskLoadWorker


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("DeepSeek 网页批量任务")
        self.resize(1820, 900)
        self.settings = QSettings("codex-local", "deepseek-batch-gui")
        self.tasks: Deque[PromptTask] = deque()
        self.task_rows: dict[str, int] = {}
        self.accounts: list[AccountPane] = []
        self.runners: list[AccountRunner] = []
        self.task_load_thread: QThread | None = None
        self.task_load_worker: TaskLoadWorker | None = None
        self.result_threads: list[QThread] = []
        self.result_workers: list[ResultWriteWorker] = []
        self.hook_threads: list[QThread] = []
        self.hook_workers: list[CheckHookWorker] = []
        self.hook_retries: dict[str, int] = {}
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
        self.check_hook_edit = QLineEdit("", self)
        self.check_hook_edit.setPlaceholderText("可选：任务完成后运行的 Python 检查脚本")
        for edit in (
            self.chat_url_edit,
            self.prompts_dir_edit,
            self.output_dir_edit,
            self.runtime_dir_edit,
            self.check_hook_edit,
        ):
            edit.setMinimumWidth(60)

        self.prompt_browse_button = QPushButton("…", self)
        self.output_browse_button = QPushButton("…", self)
        self.runtime_browse_button = QPushButton("…", self)
        self.check_hook_browse_button = QPushButton("…", self)
        for btn in (
            self.prompt_browse_button,
            self.output_browse_button,
            self.runtime_browse_button,
            self.check_hook_browse_button,
        ):
            btn.setFixedWidth(28)
            btn.setToolTip("选择…")

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
        self.chat_mode_combo.addItem("保持当前页面", "keep")

        self.search_check = QCheckBox("启用网页搜索", self)
        self.search_check.setChecked(True)
        self.require_search_check = QCheckBox("找不到搜索按钮时暂停账号", self)
        self.require_search_check.setChecked(True)
        self.deepthink_check = QCheckBox("启用深度思考", self)
        self.new_chat_check = QCheckBox("每个任务前新对话", self)
        self.new_chat_check.setChecked(True)
        self.check_hook_enabled_check = QCheckBox("任务完成后运行检查脚本", self)
        self.check_hook_max_retries_spin = QSpinBox(self)
        self.check_hook_max_retries_spin.setRange(0, 20)
        self.check_hook_max_retries_spin.setValue(2)
        self.check_hook_max_retries_spin.setSuffix(" 次")

        spin_max_width = 110
        for spin in (
            self.account_count_spin,
            self.min_delay_spin,
            self.max_delay_spin,
            self.poll_interval_spin,
            self.stable_seconds_spin,
            self.reply_timeout_spin,
            self.check_hook_max_retries_spin,
        ):
            spin.setMaximumWidth(spin_max_width)
        self.chat_mode_combo.setMaximumWidth(140)

        self.apply_accounts_button = QPushButton("应用账号数", self)
        self.reload_all_button = QPushButton("全部打开", self)
        self.probe_layout_button = QPushButton("探测布局", self)
        self.load_tasks_button = QPushButton("加载任务", self)
        self.start_button = QPushButton("开始", self)
        self.stop_button = QPushButton("停止", self)
        self.stop_button.setEnabled(False)

        path_grid = QGridLayout()
        path_grid.setHorizontalSpacing(6)
        path_grid.addWidget(QLabel("地址", self), 0, 0)
        path_grid.addWidget(self.chat_url_edit, 0, 1)
        path_grid.addWidget(QLabel("Prompt", self), 1, 0)
        path_grid.addWidget(self.prompts_dir_edit, 1, 1)
        path_grid.addWidget(self.prompt_browse_button, 1, 2)
        path_grid.addWidget(QLabel("输出", self), 2, 0)
        path_grid.addWidget(self.output_dir_edit, 2, 1)
        path_grid.addWidget(self.output_browse_button, 2, 2)
        path_grid.addWidget(QLabel("运行时", self), 3, 0)
        path_grid.addWidget(self.runtime_dir_edit, 3, 1)
        path_grid.addWidget(self.runtime_browse_button, 3, 2)
        path_grid.addWidget(QLabel("检查脚本", self), 4, 0)
        path_grid.addWidget(self.check_hook_edit, 4, 1)
        path_grid.addWidget(self.check_hook_browse_button, 4, 2)
        path_grid.setColumnStretch(1, 1)

        settings_grid = QGridLayout()
        settings_grid.setHorizontalSpacing(8)
        settings_pairs = [
            ("账号数", self.account_count_spin),
            ("对话模式", self.chat_mode_combo),
            ("最小间隔", self.min_delay_spin),
            ("最大间隔", self.max_delay_spin),
            ("轮询间隔", self.poll_interval_spin),
            ("回复稳定", self.stable_seconds_spin),
            ("回复超时", self.reply_timeout_spin),
            ("重试上限", self.check_hook_max_retries_spin),
        ]
        for index, (text, widget) in enumerate(settings_pairs):
            row, col = divmod(index, 2)
            settings_grid.addWidget(QLabel(text, self), row, col * 2)
            settings_grid.addWidget(widget, row, col * 2 + 1)
        settings_grid.setColumnStretch(1, 1)
        settings_grid.setColumnStretch(3, 1)

        mode_layout = QVBoxLayout()
        mode_layout.setSpacing(4)
        mode_layout.addWidget(self.search_check)
        mode_layout.addWidget(self.require_search_check)
        mode_layout.addWidget(self.deepthink_check)
        mode_layout.addWidget(self.new_chat_check)
        mode_layout.addWidget(self.check_hook_enabled_check)

        control_grid = QGridLayout()
        control_grid.setHorizontalSpacing(6)
        control_grid.addWidget(self.apply_accounts_button, 0, 0)
        control_grid.addWidget(self.reload_all_button, 0, 1)
        control_grid.addWidget(self.probe_layout_button, 0, 2)
        control_grid.addWidget(self.load_tasks_button, 1, 0)
        control_grid.addWidget(self.start_button, 1, 1)
        control_grid.addWidget(self.stop_button, 1, 2)

        config_box = QGroupBox("配置", self)
        config_layout = QVBoxLayout(config_box)
        config_layout.setSpacing(8)
        config_layout.addLayout(path_grid)
        config_layout.addLayout(settings_grid)
        config_layout.addLayout(mode_layout)
        config_layout.addLayout(control_grid)

        self.task_table = QTableWidget(0, 4, self)
        self.task_table.setHorizontalHeaderLabels(["任务 ID", "状态", "账号", "Prompt 文件"])
        self.task_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.task_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.task_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.task_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)

        self.stats_table = QTableWidget(0, 5, self)
        self.stats_table.setHorizontalHeaderLabels(["账号", "已分配", "成功", "失败", "平均耗时"])
        self.stats_table.verticalHeader().setVisible(False)
        self.stats_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.stats_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.stats_table.setMinimumWidth(0)
        self.stats_table.horizontalHeader().setMinimumSectionSize(30)
        for col in range(5):
            mode = (
                QHeaderView.ResizeMode.Stretch
                if col == 4
                else QHeaderView.ResizeMode.ResizeToContents
            )
            self.stats_table.horizontalHeader().setSectionResizeMode(col, mode)

        stats_box = QGroupBox("账号使用情况", self)
        stats_layout = QVBoxLayout(stats_box)
        stats_layout.setContentsMargins(6, 6, 6, 6)
        stats_layout.addWidget(self.stats_table)

        self.log_edit = QPlainTextEdit(self)
        self.log_edit.setReadOnly(True)
        self.log_edit.setMaximumBlockCount(2000)
        log_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self.log_edit.setFont(log_font)
        log_char_width = QFontMetrics(log_font).horizontalAdvance("M")
        log_default_width = log_char_width * 80 + 24

        self.left_splitter = QSplitter(Qt.Orientation.Vertical, self)
        self.left_splitter.addWidget(config_box)
        self.left_splitter.addWidget(stats_box)
        self.left_splitter.setStretchFactor(0, 1)
        self.left_splitter.setStretchFactor(1, 0)
        self.left_splitter.setSizes([600, 240])

        self.account_tabs = QTabWidget(self)

        log_panel = QWidget(self)
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.addWidget(QLabel("日志", self))
        log_layout.addWidget(self.log_edit, 1)

        task_panel = QWidget(self)
        task_layout = QVBoxLayout(task_panel)
        task_layout.setContentsMargins(0, 0, 0, 0)
        task_layout.addWidget(QLabel("任务", self))
        task_layout.addWidget(self.task_table, 1)

        self.right_splitter = QSplitter(Qt.Orientation.Vertical, self)
        self.right_splitter.addWidget(log_panel)
        self.right_splitter.addWidget(task_panel)
        self.right_splitter.setStretchFactor(0, 1)
        self.right_splitter.setStretchFactor(1, 1)
        self.right_splitter.setSizes([460, 360])

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.main_splitter.addWidget(self.left_splitter)
        self.main_splitter.addWidget(self.account_tabs)
        self.main_splitter.addWidget(self.right_splitter)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setStretchFactor(2, 0)
        self.main_splitter.setSizes([300, 960, log_default_width + 60])

        main_layout = QVBoxLayout(central)
        main_layout.addWidget(self.main_splitter, 1)

        self.prompt_browse_button.clicked.connect(lambda: self._choose_dir(self.prompts_dir_edit))
        self.output_browse_button.clicked.connect(lambda: self._choose_dir(self.output_dir_edit))
        self.runtime_browse_button.clicked.connect(lambda: self._choose_dir(self.runtime_dir_edit))
        self.check_hook_browse_button.clicked.connect(self._choose_check_hook_file)
        self.apply_accounts_button.clicked.connect(self.rebuild_accounts)
        self.reload_all_button.clicked.connect(self.reload_all_accounts)
        self.probe_layout_button.clicked.connect(self.probe_current_layout)
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
        self.check_hook_edit.setText(str(self.settings.value("check_hook_path", "")))
        self.check_hook_enabled_check.setChecked(
            str(self.settings.value("check_hook_enabled", "false")).lower() == "true"
        )
        self.check_hook_max_retries_spin.setValue(int(self.settings.value("check_hook_max_retries", 2)))
        for splitter, key in (
            (self.main_splitter, "splitter_main_state"),
            (self.left_splitter, "splitter_left_state"),
            (self.right_splitter, "splitter_right_state"),
        ):
            state = self.settings.value(key)
            if state:
                splitter.restoreState(state)

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
        self.settings.setValue("check_hook_path", self.check_hook_edit.text().strip())
        self.settings.setValue("check_hook_enabled", self.check_hook_enabled_check.isChecked())
        self.settings.setValue("check_hook_max_retries", self.check_hook_max_retries_spin.value())
        self.settings.setValue("splitter_main_state", self.main_splitter.saveState())
        self.settings.setValue("splitter_left_state", self.left_splitter.saveState())
        self.settings.setValue("splitter_right_state", self.right_splitter.saveState())
        self.settings.sync()

    def _choose_dir(self, target: QLineEdit) -> None:
        start = target.text().strip() or str(PROJECT_DIR)
        directory = QFileDialog.getExistingDirectory(self, "选择目录", start)
        if directory:
            target.setText(directory)

    def _choose_check_hook_file(self) -> None:
        start = self.check_hook_edit.text().strip() or str(PROJECT_DIR)
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择检查脚本",
            start,
            "Python 脚本 (*.py);;所有文件 (*)",
        )
        if path:
            self.check_hook_edit.setText(path)

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

    def check_hook_enabled(self) -> bool:
        return self.check_hook_enabled_check.isChecked() and bool(
            self.check_hook_edit.text().strip()
        )

    def check_hook_path(self) -> Path | None:
        text = self.check_hook_edit.text().strip()
        if not text:
            return None
        return Path(text).expanduser().resolve()

    def check_hook_max_retries(self) -> int:
        return self.check_hook_max_retries_spin.value()

    def bump_hook_retry(self, task_id: str) -> int:
        self.hook_retries[task_id] = self.hook_retries.get(task_id, 0) + 1
        return self.hook_retries[task_id]

    def reset_hook_retry(self, task_id: str) -> None:
        self.hook_retries.pop(task_id, None)

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
            runner.stats_updated.connect(self._runner_stats_updated)
            self.accounts.append(account)
            self.runners.append(runner)
            self.account_tabs.addTab(account, f"账号 {account_id}")
        self._refresh_stats_table()
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

    def probe_current_layout(self) -> None:
        widget = self.account_tabs.currentWidget()
        if not isinstance(widget, AccountPane):
            QMessageBox.information(self, "没有账号", "当前没有可探测的账号页面。")
            return

        self._save_settings()
        self.probe_layout_button.setEnabled(False)
        self.log(f"正在探测账号 {widget.account_id} 当前页面布局。")
        widget.run_js(
            js_call("probeLayout"),
            lambda result, account=widget: self._layout_probe_done(account, result),
        )

    def _layout_probe_done(self, account: AccountPane, result: dict[str, Any]) -> None:
        self.probe_layout_button.setEnabled(True)
        if not result.get("ok"):
            error = result.get("error") or "未知错误"
            self.log(f"账号 {account.account_id} 布局探测失败：{error}")
            QMessageBox.warning(self, "布局探测失败", str(error))
            return

        probe_dir = self.runtime_dir() / "layout_probe"
        probe_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = probe_dir / f"account_{account.account_id}_{timestamp}.json"
        data = {
            "account_id": account.account_id,
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "probe": result,
        }
        output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self.log(f"账号 {account.account_id} 布局探测已写入：{output_path}")

    def load_tasks(self) -> None:
        if self.task_load_thread is not None:
            return

        self._save_settings()
        self.tasks.clear()
        self.task_rows.clear()
        self.hook_retries.clear()
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

    def run_check_hook_async(
        self,
        task: PromptTask,
        output_path: Path,
        on_done: Callable[[CheckHookResult], None],
        on_failed: Callable[[str, str], None],
    ) -> None:
        hook_path = self.check_hook_path()
        if hook_path is None:
            on_failed(task.task_id, "未配置检查脚本路径")
            return

        thread = QThread(self)
        worker = CheckHookWorker(
            task_id=task.task_id,
            hook_path=hook_path,
            output_path=output_path,
            prompt_path=task.prompt_path,
        )
        worker.moveToThread(thread)

        worker.finished.connect(on_done)
        worker.failed.connect(on_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.started.connect(worker.run)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(lambda: self._cleanup_hook_worker(thread, worker))

        self.hook_threads.append(thread)
        self.hook_workers.append(worker)
        thread.start()

    def _cleanup_hook_worker(self, thread: QThread, worker: CheckHookWorker) -> None:
        if thread in self.hook_threads:
            self.hook_threads.remove(thread)
        if worker in self.hook_workers:
            self.hook_workers.remove(worker)

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

    def _runner_stats_updated(self, account_id: int) -> None:
        for row, runner in enumerate(self.runners):
            if runner.account.account_id == account_id:
                self._refresh_stats_row(row, runner)
                return

    def _refresh_stats_table(self) -> None:
        self.stats_table.setRowCount(len(self.runners))
        for row, runner in enumerate(self.runners):
            self._refresh_stats_row(row, runner)

    def _refresh_stats_row(self, row: int, runner: AccountRunner) -> None:
        values = [
            str(runner.account.account_id),
            str(runner.assigned_count),
            str(runner.success_count),
            str(runner.failure_count),
            self._format_duration(runner.avg_success_seconds),
        ]
        for col, text in enumerate(values):
            item = self.stats_table.item(row, col)
            if item is None:
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.stats_table.setItem(row, col, item)
            else:
                item.setText(text)

    @staticmethod
    def _format_duration(seconds: float) -> str:
        if seconds <= 0:
            return "-"
        if seconds < 60:
            return f"{seconds:.1f} 秒"
        minutes = seconds / 60
        if minutes < 60:
            return f"{minutes:.1f} 分"
        return f"{minutes / 60:.1f} 时"

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
        for thread in list(self.hook_threads):
            thread.quit()
            thread.wait(1500)
        self._dispose_runners()
        self._dispose_accounts()
        super().closeEvent(event)
