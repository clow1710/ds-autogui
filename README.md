# DeepSeek 网页批量任务 GUI

这是一个 Ubuntu 桌面环境下使用 PySide6 和内置 WebView 的批量任务工具。它不会保存账号密码；每个账号使用独立的 `QWebEngineProfile`，cookie、localStorage、IndexedDB 等登录态会持久化到运行时目录。

## 安装

Linux：

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

如果 Ubuntu 缺少 Qt WebEngine 运行库，可先安装常见桌面依赖：

```bash
sudo apt install libxcb-cursor0 libnss3 libxkbcommon-x11-0
```

Windows PowerShell：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 运行

Linux：

```bash
. .venv/bin/activate
python main.py
```

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
python main.py
```

默认目录：

- `prompts/`：输入目录，每个 `*.prompt` 文件是一条任务，文件名不含扩展名的部分作为任务 ID。
- `outputs/`：输出目录，成功响应写入 `{任务ID}.json`。
- `runtime/`：WebView 持久化数据目录，包含各账号独立的 profile 与缓存。
- `data/`：当前仓库中的原始数据目录，GUI 不会自动读取；需要先自行生成 `prompts/*.prompt`。

## 代码结构

- `main.py`：只负责把 `src` 加到 import path 并启动应用。
- `src/deepseek_batch/window.py`：主窗口、任务队列和线程调度。
- `src/deepseek_batch/account.py`：每个账号独立的 WebView/Profile。
- `src/deepseek_batch/runner.py`：单账号任务执行状态机。
- `src/deepseek_batch/workers.py`：后台线程 worker，负责扫描 prompt 和写 JSON。
- `src/deepseek_batch/js_bridge.py`：页面内自动化脚本。

Qt WebEngine 要求 WebView 和页面操作留在 GUI 线程；本程序把目录扫描、prompt 读取、结果 JSON 写入放到 `QThread`，网页操作使用异步 JS 回调和 `QTimer`，避免阻塞界面事件循环。

## 使用流程

1. 启动程序，设置账号数，点击“应用账号数”。
2. 在每个账号标签页里手动登录 DeepSeek。
3. 将任务文件放入 `prompts/`，例如 `company_001.prompt`。
4. 点击“加载任务”，确认任务表。
5. 按任务需要选择“专家模式”或“快速模式”，保持“启用智能搜索（网页搜索）”勾选，点击“开始”。

输出 JSON 格式示例：

```json
{
  "task_id": "company_001",
  "account_id": 1,
  "request_time": "2026-05-13T17:30:00+08:00",
  "response_time": "2026-05-13T17:31:12+08:00",
  "prompt": "...",
  "response": "..."
}
```

## 注意

- 程序不会处理验证码、风控、登录失效或服务端繁忙提示；遇到这些情况需要在对应 WebView 中手动处理。
- 网页自动化依赖 DeepSeek 当前页面结构。若 DeepSeek 更新了按钮文本或 DOM，可能需要取消“找不到网页搜索按钮时暂停账号”后临时运行，或调整源码中的关键词列表。
- 已存在同名输出 JSON 的任务会在加载时自动跳过，便于断点续跑。
- Qt WebEngine 可能输出 `local-network-access`、`get webid error, use local webid` 或 `ssl_client_socket_impl.cc` / `net_error -100` 这类页面和 Chromium 运行时日志；如果 DeepSeek 页面能正常加载、发送和收取回复，通常不需要修改程序。

## 生成公司核验 Prompt

仓库内置了公司注册地址与企业类型核验模板：

- 模板：`prompt_templates/company_registry_lookup.md`
- 生成脚本：`scripts/generate_company_prompts.py`
- 默认输入：`data/companies.csv`
- 默认输出：`prompts/*.prompt`

生成全部 prompt：

```bash
python3 scripts/generate_company_prompts.py
```

脚本默认每 10 家公司生成一个 prompt 文件；如果最后一批不足 10 条，会保留为最后一个 prompt，避免丢失数据。模板中已经固定了企业类型词典、人工核查标记和 JSON 输出结构，可直接编辑 `prompt_templates/company_registry_lookup.md` 调整。

常用参数：

```bash
# 只预览前 10 条，不写文件
python3 scripts/generate_company_prompts.py --limit 10 --dry-run --preview

# 覆盖已生成的同名 prompt
python3 scripts/generate_company_prompts.py --overwrite

# 变更批大小或输出目录
python3 scripts/generate_company_prompts.py --batch-size 10 --output-dir prompts
```
