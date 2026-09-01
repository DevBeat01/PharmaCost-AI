# 代码审查报告 — 制药成本智能分析报告系统

> 审查日期: 2026-08-25
> 审查范围: 全部28个源代码文件

---

## 审查总览

| 类别 | 问题数 | P0(阻塞) | P1(重要) | P2(建议) |
|------|--------|----------|----------|----------|
| Logic & Correctness | 3 | 1 | 2 | 0 |
| Security | 2 | 0 | 1 | 1 |
| Performance | 2 | 0 | 1 | 1 |
| Readability | 2 | 0 | 0 | 2 |
| Error Handling | 2 | 0 | 1 | 1 |
| **合计** | **11** | **1** | **5** | **5** |

---

## P0 Issues (Must Fix)

### [issue] BUG-001: `_tasks` 内存泄漏 — 报告任务状态永不清理

**文件**: `routers/report.py:10`

```python
_tasks: dict = {}  # 永远增长，无清理机制
```

**问题**: 每次调用 `/api/report/generate` 都会向 `_tasks` 字典添加条目，但从不清理。长时间运行后内存会持续增长。

**修复**: 添加过期清理机制，或使用带TTL的缓存。

---

## P1 Issues (Should Fix)

### [issue] BUG-002: `MONTHS.index()` 未捕获 ValueError

**文件**: `data/cost_data.py:89,156,181,208`

```python
idx = MONTHS.index(month) if month in MONTHS else -1
```

**问题**: 如果传入不在 `MONTHS` 列表中的月份（如用户输入错误），`month in MONTHS` 检查是正确的，但整个表达式在 `month` 为 `None` 时会抛出 `TypeError`。

**修复**: 添加类型检查。

### [issue] BUG-003: `benchmark_breakdown` 除零风险

**文件**: `analysis/benchmark.py:69`

```python
contribution = round(r['diff_amount'] / total_diff * 100, 1) if total_diff else 0
```

**问题**: 当三个要素差异恰好抵消（total_diff = 0.0）时，浮点精度可能导致 `total_diff` 为极小值而非精确0，触发除零。

**修复**: 使用 `abs(total_diff) < 0.001` 替代 `total_diff`。

### [issue] BUG-004: RPA客户端缺少HTTP错误状态码检查

**文件**: `rpa/client.py:34,42,52,60,67`

```python
resp = await client.post(f"{RPA_BASE_URL}/api/rpa/tasks", json=task_data)
return resp.json()  # 未检查 resp.status_code
```

**问题**: 如果RPA服务返回4xx/5xx错误，直接调用 `.json()` 可能失败或返回错误信息但未被识别。

**修复**: 添加 `resp.raise_for_status()` 或检查状态码。

### [issue] BUG-005: `cost_structure` 除零风险

**文件**: `analysis/dashboard.py:104`

```python
total = current['material_cost'] + current['labor_cost'] + current['overhead_cost']
```

**问题**: 如果三个成本要素都为0（理论上不可能，但防御性编程应考虑），后续除法会除零。

**修复**: 添加 `total > 0` 检查。

---

## P2 Issues (Nice to Have)

### [suggestion] STYLE-001: `sys.path.insert` 重复出现在每个模块

**影响文件**: 几乎所有 `app/` 下的 `.py` 文件

```python
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
```

**建议**: 使用相对导入或在 `setup.py`/`pyproject.toml` 中配置包结构，避免每个文件都修改 `sys.path`。

### [suggestion] STYLE-002: `config.py` 中路径使用硬编码中文目录名

**文件**: `config.py:11`

```python
DATA_DIR = BASE_DIR / "创灵境_考题模拟数据"
```

**建议**: 可通过环境变量覆盖，增加部署灵活性。

### [suggestion] PERF-001: `get_market_prices` 每次调用都遍历全部行

**文件**: `data/cost_data.py:257-278`

**问题**: 每次调用都遍历13种药材×6个月的数据构建字典，如果频繁调用会有性能开销。

**建议**: 在 `load_all()` 时预处理为字典缓存。

### [suggestion] PERF-002: `hybrid_search` 中字符串切片作为去重key

**文件**: `rag/retriever.py:81,88`

```python
key = r['content'][:100]
```

**问题**: 用前100字符作为去重key，如果两个不同chunk的前100字符相同会误合并。

**建议**: 使用 `source + chunk_index` 作为唯一key。

### [suggestion] STYLE-003: `llm_client` 全局实例在模块加载时创建

**文件**: `llm/client.py:53`

```python
llm_client = LLMClient()
```

**问题**: 如果 API Key 未配置，模块导入时就会创建客户端实例（虽然不会立即报错，但会在首次调用时失败）。

**建议**: 使用延迟初始化。

---

## 已修复的Bug（本轮测试中发现并修复）

| Bug | 描述 | 状态 |
|-----|------|------|
| BUG-FIX-001 | `report/engine.py` 硬导入 chromadb 导致崩溃 | ✅ 已修复 |
| BUG-FIX-002 | `_safe_llm_call` 未检查依赖可用性 | ✅ 已修复 |
| BUG-FIX-003 | TestClient 不触发 startup 事件 | ✅ 已修复(测试中) |

---

## 代码质量评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 架构设计 | ⭐⭐⭐⭐ | 模块化清晰，分层合理 |
| 错误处理 | ⭐⭐⭐ | 关键路径有try/except，但部分缺少状态码检查 |
| 代码风格 | ⭐⭐⭐⭐ | 一致性好，命名规范 |
| 测试覆盖 | ⭐⭐⭐ | 核心计算有测试，但缺少集成测试 |
| 安全性 | ⭐⭐⭐⭐ | 无硬编码密钥，输入验证基本到位 |
| **综合** | **⭐⭐⭐⭐** | **整体质量良好，有少量需修复的问题** |

---

## 修复优先级建议

1. **立即修复**: BUG-003 (除零风险), BUG-004 (HTTP错误检查)
2. **尽快修复**: BUG-001 (内存泄漏), BUG-002 (类型检查)
3. **后续优化**: STYLE/PERF 建议项
