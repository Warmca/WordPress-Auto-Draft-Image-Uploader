# Repository Guidelines

## 中文版本

### 项目结构与模块组织
本仓库是单脚本 Python 工具。核心代码在 `WP上传写真预览下自动草稿功能.py`，说明文档在 `WP上传写真预览下自动草稿功能.readme.MD`。脚本运行后会在同目录生成 `wp_upload_history.db` 和 `wp_uploader.log` 等运行产物；图片来源目录由 `Config.BASE_DIRECTORY` 指向，通常不放入仓库。

### 构建、测试与开发命令
```powershell
python -m venv venv
.\venv\Scripts\activate
pip install aiohttp pillow tqdm pypinyin
python -m py_compile "WP上传写真预览下自动草稿功能.py"
python "WP上传写真预览下自动草稿功能.py"
```
`py_compile` 用于快速检查语法；最后一条命令会连接 WordPress REST API 并执行实际上传，运行前确认配置和目标目录。

### 编码风格与命名约定
使用 Python 3.8+、UTF-8、4 空格缩进。类名采用 `PascalCase`，函数、变量和配置项采用 `snake_case` 或现有的全大写常量风格。保持现有单文件组织，新增逻辑优先放入相邻职责类中，例如上传逻辑放在 `AsyncUploader`，文章逻辑放在 `PostCreator`。

### 测试指南
当前没有测试目录。新增测试时建议使用 `pytest`，放在 `tests/test_*.py`。测试应 mock WordPress HTTP 请求，使用临时 SQLite 数据库和临时图片文件，避免直接访问真实站点。

### 提交与 PR 指南
当前目录没有 Git 历史；提交信息建议使用祈使句并带范围，例如 `fix uploader retry handling` 或 `docs update setup notes`。PR 应说明变更目的、验证命令、配置影响；涉及上传流程时附上日志片段，避免提交真实凭据、数据库、日志或本地图片。

### 安全与配置提示
不要把 WordPress 用户名、应用密码和域名硬编码到公开提交中。优先从环境变量或本地未跟踪配置文件读取敏感配置，并在泄露后立即轮换应用密码。

## English Version

### Project Structure & Module Organization
This repository is a single-script Python tool. The main source file is `WP上传写真预览下自动草稿功能.py`, and the usage notes live in `WP上传写真预览下自动草稿功能.readme.MD`. Runtime files such as `wp_upload_history.db` and `wp_uploader.log` are generated next to the script. Image input is controlled by `Config.BASE_DIRECTORY` and should normally stay outside the repository.

### Build, Test, and Development Commands
```powershell
python -m venv venv
.\venv\Scripts\activate
pip install aiohttp pillow tqdm pypinyin
python -m py_compile "WP上传写真预览下自动草稿功能.py"
python "WP上传写真预览下自动草稿功能.py"
```
Use `py_compile` for a quick syntax check. Running the script contacts the WordPress REST API and performs real uploads, so confirm configuration and input paths first.

### Coding Style & Naming Conventions
Use Python 3.8+, UTF-8, and 4-space indentation. Keep class names in `PascalCase`; use `snake_case` for functions and variables, and preserve the existing uppercase style for constants. Keep related changes near their owning class, such as upload behavior in `AsyncUploader` and post creation in `PostCreator`.

### Testing Guidelines
There is no test suite yet. Add tests under `tests/test_*.py`, preferably with `pytest`. Mock WordPress HTTP calls, use temporary SQLite databases, and create temporary image fixtures so tests do not depend on a live site.

### Commit & Pull Request Guidelines
No Git history is present in this directory, so use concise imperative commit messages such as `fix uploader retry handling` or `docs update setup notes`. PRs should describe the goal, validation commands, and configuration impact. For upload-flow changes, include relevant log excerpts and never include real credentials, databases, logs, or local images.

### Security & Configuration Tips
Do not commit WordPress usernames, application passwords, or domains as public defaults. Prefer environment variables or an untracked local config file for sensitive values, and rotate application passwords immediately after exposure.
