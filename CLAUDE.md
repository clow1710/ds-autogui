# CLAUDE.md

本文件给 Claude Code 在此仓库工作时提供上下文。日常使用说明见 `README.md`。

## 项目概述

PySide6 + QWebEngine 的桌面 GUI，用浏览器自动化批量跑 DeepSeek 网页对话。不存账号密码：每个账号一个 `QWebEngineProfile`，cookie / localStorage / IndexedDB 持久化到 `runtime/`，用户首次手动登录后续可复用登录态。

入口：`main.py` 把 `src/` 加到 `sys.path`，再调 `deepseek_batch.app.main()`。

## 架构

GUI 线程跑 Qt 事件循环和所有 WebView 操作；IO 用 `QThread` worker 隔离。

- `app.py`：`QApplication` 启动。
- `window.py`：`MainWindow` — 配置、任务表、日志、`QTabWidget` 容纳账号，持有任务队列 (`deque[PromptTask]`) 和 `AccountRunner` 列表。
- `account.py`：`AccountPane` — 一个账号一个 `QWebEngineProfile` + `QWebEnginePage` + `QWebEngineView`，profile/cache 路径在 `runtime/profiles/account_{N}` / `runtime/cache/account_{N}`。
- `runner.py`：`AccountRunner` — 单账号状态机（取任务 → 新对话 → 切模式 → 发送 → 轮询回复 → 写结果 → 随机延时 → 下一个任务），完全由 `QTimer.singleShot` 驱动，不阻塞 GUI 线程。
- `workers.py`：`TaskLoadWorker` 扫 prompt 目录、`ResultWriteWorker` 写 JSON，都 `moveToThread(QThread)`。
- `js_bridge.py`：注入页面的自动化脚本（`AUTOMATION_JS`）和 `js_call(fn, *args)` 包装器；脚本里所有功能挂在 `window.__deepseekBatchBot` 上，靠 `BOT_VERSION` 字符串判断是否需要重新注入。
- `models.py`：`@dataclass(frozen=True)` 数据类（`PromptTask` / `TaskRow` / `TaskLoadResult` / `ResultPayload` / `ResultWriteResult`）。
- `config.py`：默认路径、聊天 URL、UI 词条（搜索 / 深度思考 / 新对话按钮的中英候选词）。
- `utils.py`：`now_iso()` 和 `enum_value()`（兼容 PySide6 不同版本的 enum 命名空间）。

`main.py → app.main() → MainWindow → AccountPane × N + AccountRunner × N`。任务来源是 `prompts/*.prompt`，结果落在 `outputs/{task_id}.json`，已存在的 task 加载时跳过 → 天然支持断点续跑。

## 关键约定

- **GUI/线程边界**：QWebEngine 的 `view`/`page`/`runJavaScript` 必须留在 GUI 线程；只有目录扫描和结果落盘走 `QThread` worker。新代码改这块前先看 `runner.py` 怎么用 `QTimer.singleShot` 串异步流程。
- **状态机继续/中止**：`AccountRunner` 每个回调一开始都检查 `self.running` 和 `self.current_task is not None`，避免停止后旧回调还在跑。新增步骤要遵守这个模式。
- **任务重排**：失败时 `window.requeue_task(task)` 把任务塞回 `tasks` 队首（`appendleft`），不是丢掉。
- **JS 注入幂等**：要改 `AUTOMATION_JS` 行为时一定把 `BOT_VERSION` 改了，否则旧页面会复用旧脚本。
- **JS 结果协议**：`js_bridge.parse_js_result` 期望返回 `{ok: bool, ...}`，失败放 `error` / `warning` / `retry`。Python 这边按这些字段分支。
- **设置持久化**：`QSettings("codex-local", "deepseek-batch-gui")` 持久化所有 UI 选项，`rebuild_accounts` / `load_tasks` / `start_batch` / `closeEvent` 会调 `_save_settings()`。
- **Frozen dataclass**：数据类都是 `frozen=True`，要改字段就新建实例，别原地改。
- **错误处理**：worker 内部用 `except Exception as exc: emit failed(...)`（带 `# noqa: BLE001`）把异常通过信号回传 GUI；GUI 内部不要照抄这种宽 `except`。

## 运行 / 开发

```powershell
.\.venv\Scripts\Activate.ps1
python main.py
```

需要 Python ≥ 3.13、PySide6 ≥ 6.11。Linux 还要装 `libxcb-cursor0 libnss3 libxkbcommon-x11-0`。

仓库无单元测试，无 lint/format 配置；改动靠手动跑 GUI 验证。改 GUI 行为后请实际启动一次确认页面没崩、Qt WebEngine 日志没新增报错。

## 工作目录布局

- `prompts/`：输入，`{task_id}.prompt` 一个任务（gitignored，仅保留 `.gitkeep`）。
- `outputs/`：输出 JSON（gitignored）。
- `runtime/`：账号 profile / cache / 布局探测（gitignored）。
- `data/`：原始 CSV，不会被 GUI 自动读取，要用 `scripts/generate_company_prompts.py` 生成 prompt。
- `prompt_templates/`：prompt 模板，目前有公司注册地址核验模板。
- `tasks/`：本地任务草稿（gitignored）。

## DeepSeek 页面相关易坏点

- DeepSeek 改 DOM / 按钮文案就会让 `js_bridge.py` 失效。可点 GUI 上的"探测当前布局"按钮，结果写到 `runtime/layout_probe/`，对比定位选择器。
- 候选词列表在 `config.py`（`SEARCH_TERMS` / `DEEPTHINK_TERMS` / `NEW_CHAT_TERMS`）；新增语种或文案先加这里。
- 不要尝试自动处理验证码 / 风控 / 登录失效，按设计这些就该在对应 WebView 里手动解决。

## 提交风格

近期提交是中文短句陈述事实，如「修复新对话按钮检测与回复解析」「增加 DOM 探测功能」。沿用即可。
