# PharmaCost-AI

面向制药企业成本管理的智能分析原型系统。系统以 ERP 成本数据为基础，结合 RAG 知识库、DeepSeek/MiMo 文本生成和模拟 RPA 服务，自动生成可追溯的成本洞察、分析报告与整改任务。

## 功能模块

- **智能报告生成**：解析 Word 模板，融合成本数据和 RAG 检索结果，生成并导出 Word/PDF 报告。
- **成本看板**：展示趋势、成本结构、瀑布图、热力图、原材料与制造费用明细，并生成归因分析。
- **对标分析**：按“找差异、拆结构、拆原因”对比中药一厂与中药二厂。
- **整改闭环**：将分析建议转成结构化任务，调用模拟 RPA 并追踪状态。
- **设置中心**：管理导入的成本 CSV、知识文档和报告模板；所有使用 RAG 的产出都显示来源。

## 技术栈

FastAPI、Pandas、Chroma、百炼 Embedding API、BM25、DeepSeek（主）/MiMo（备用）、SQLite、ECharts、原生 HTML/CSS/JavaScript、Docker Compose。

## 目录

```text
app/                 FastAPI 应用、前端资源、RAG、报告和任务逻辑
rpa_mock/            可独立运行的模拟 RPA 服务
tests/tests/         功能与回归测试
创灵境_考题模拟数据/  比赛数据包（本地提供，不提交到仓库）
```

## 准备数据与配置

比赛数据包不包含在仓库中。请从比赛材料或授权的私有渠道取得，并解压到项目根目录：

```text
创灵境_考题模拟数据/
  01_成本明细数据/
  02_行业参考数据/
  03_制药知识文档/
  04_报告模板/
```

复制 `app/.env.example` 为 `app/.env`，然后填入自己的密钥。`DEEPSEEK_API_KEY` 是主模型密钥，`MIMO_API_KEY` 用于主模型不可用时自动备用。`app/.env` 已被 Git 忽略，切勿提交。

```powershell
Copy-Item app/.env.example app/.env
E:\Anacounda\python.exe -m pip install -r app/requirements.txt
```

## 评审一键启动（Windows）

评委只需准备比赛数据包并双击项目根目录的 `start_all.bat`。脚本会自动检查 Python、首次安装依赖、启动主应用和模拟 RPA 服务，等待健康检查通过后自动打开浏览器。

- 默认使用 `E:\Anacounda\python.exe`；其他环境可将 `PHARMACOST_PYTHON` 设置为 Python 完整路径后再双击。
- 首次构建知识库会调用百炼 Embedding API；后续启动复用已生成的本地索引。
- 不配置模型 API Key 时，基础数据看板和对标数据仍可使用；AI 归因、任务生成和智能报告需在 `app/.env` 配置可用密钥。
- 服务启动后访问 <http://127.0.0.1:8000>；关闭两个标题为 `PharmaCost-Web` 与 `RPA-Server` 的命令窗口即可停止服务。

## 本地启动

在项目根目录运行 `start_all.bat`，它会启动：

- 主应用：<http://127.0.0.1:8000>
- API 文档：<http://127.0.0.1:8000/docs>
- RPA Mock：<http://127.0.0.1:8090/docs>

也可分别运行 `start.bat` 与 `start_rpa.bat`。首次启动会在后台构建 RAG 索引；Chroma 索引、SQLite 任务库和报告输出都仅保留在本地，不会提交到 Git。

## Docker Compose

准备好 `app/.env` 和竞赛数据包后执行：

```powershell
docker compose up --build
```

编排会将本地 `创灵境_考题模拟数据/` 只读挂载到主应用容器的 `/data`。缺少该目录时，容器会在启动前输出明确错误并退出。Chroma、报告输出、设置和 SQLite 任务库均通过本地卷持久化。

## 开发检查

```powershell
E:\Anacounda\python.exe -m pytest tests/tests -q
```

在公开仓库或部署前，请轮换任何曾暴露过的 API 密钥，并确认 `git status --ignored` 中的 `.env`、比赛数据、索引、报告和 SQLite 文件均未被暂存。
