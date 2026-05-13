from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QUrl, Signal
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .js_bridge import parse_js_result
from .utils import enum_value


class AccountPane(QWidget):
    load_status_changed = Signal(str)

    def __init__(self, account_id: int, runtime_dir: Path, chat_url: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.account_id = account_id
        self.runtime_dir = runtime_dir
        self.chat_url = chat_url
        self.disposed = False
        self.profile_dir = runtime_dir / "profiles" / f"account_{account_id}"
        self.cache_dir = runtime_dir / "cache" / f"account_{account_id}"
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.profile = QWebEngineProfile(f"deepseek-account-{account_id}", self)
        self.profile.setPersistentStoragePath(str(self.profile_dir))
        self.profile.setCachePath(str(self.cache_dir))
        self.profile.setHttpCacheType(enum_value(QWebEngineProfile, "HttpCacheType", "DiskHttpCache"))
        self.profile.setPersistentCookiesPolicy(
            enum_value(QWebEngineProfile, "PersistentCookiesPolicy", "ForcePersistentCookies")
        )

        self.page = QWebEnginePage(self.profile, self)
        settings = self.page.settings()
        settings.setAttribute(enum_value(QWebEngineSettings, "WebAttribute", "LocalStorageEnabled"), True)
        settings.setAttribute(enum_value(QWebEngineSettings, "WebAttribute", "JavascriptEnabled"), True)

        self.view = QWebEngineView(self)
        self.view.setPage(self.page)
        self.status_label = QLabel("未加载", self)
        self.reload_button = QPushButton("刷新", self)
        self.open_button = QPushButton("打开 DeepSeek", self)

        top = QHBoxLayout()
        top.addWidget(QLabel(f"账号 {account_id}", self))
        top.addWidget(self.status_label, 1)
        top.addWidget(self.open_button)
        top.addWidget(self.reload_button)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.view, 1)

        self.open_button.clicked.connect(self.load_chat)
        self.reload_button.clicked.connect(self.view.reload)
        self.page.loadStarted.connect(lambda: self._set_status("加载中"))
        self.page.loadFinished.connect(self._load_finished)
        self.load_chat()

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)
        self.load_status_changed.emit(text)

    def _load_finished(self, ok: bool) -> None:
        self._set_status("已加载" if ok else "加载失败")

    def load_chat(self) -> None:
        if self.disposed:
            return
        self.view.load(QUrl(self.chat_url))

    def run_js(self, script: str, callback: Callable[[dict[str, Any]], None]) -> None:
        if self.disposed:
            callback({"ok": False, "error": "账号视图已释放"})
            return

        def done(value: Any) -> None:
            callback(parse_js_result(value))

        self.page.runJavaScript(script, done)

    def dispose(self) -> None:
        if self.disposed:
            return
        self.disposed = True
        self.view.stop()
        self.view.setPage(None)
        self.page.deleteLater()
        self.profile.deleteLater()
