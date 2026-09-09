"""模块三对标分析回归测试，可直接用 python 执行。"""
import asyncio
import sys
import types
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))

from data.cost_data import cost_service
import analysis.benchmark as benchmark_module
from analysis.benchmark import benchmark_structure_tree, benchmark_attribution, _normalize_model_attribution


cost_service.load_all()

# 结构树必须包含成本要素和原材料明细节点。
tree_result = benchmark_structure_tree("板蓝根颗粒", "2026-03")
assert tree_result["tree"]["children"]
material_node = next(n for n in tree_result["tree"]["children"] if n["name"] == "直接材料")
assert material_node["children"]
assert {"name", "unit_cost", "total_cost", "ratio"} <= set(material_node["children"][0])

# 模型可能使用三级标题、中文序号或“常规分析”别名，后端应统一规范化而非直接兜底。
variant = """一、结论摘要（整体判断）
单位成本差异已确认。

二、常规分析
- 人工成本差异需核查。

三、改进建议
1. 优化原材料采购策略
2. 复核人工效率"""
normalized = _normalize_model_attribution(variant)
assert normalized is not None
assert normalized.startswith("1. 结论摘要")
assert "2. 重点分析" in normalized
assert "3. 改进建议" in normalized
assert _normalize_model_attribution(
    "### 结论摘要\n摘要\n### 重点分析\n- 差异\n### 改进建议\n- 核查"
) is not None
table_normalized = _normalize_model_attribution(
    "## 结论摘要\n| 指标 | 结论 |\n|---|---|\n| 单位成本 | 一厂更优 |\n\n"
    "## 重点分析\n- 差异需核查\n\n## 改进建议\n- 复核数据"
)
assert table_normalized is not None
assert "|" not in table_normalized


# 用假的 RAG/LLM 模块验证归因 Prompt 的两个占位符都会被正确填充，避免网络依赖。
fake_rag_pkg = types.ModuleType("rag")
fake_rag_pkg.__path__ = []
fake_rag = types.ModuleType("rag.retriever")
fake_rag.hybrid_search = lambda query, top_k=3: [{"source": "工艺文档", "content": "提取工艺收率波动"}]
fake_llm_pkg = types.ModuleType("llm")
fake_llm_pkg.__path__ = []
fake_client = types.ModuleType("llm.client")
call_count = {"value": 0}


def fake_chat(prompt, system="", max_tokens=4000):
    call_count["value"] += 1
    return "## 结论摘要\n单位成本差异已确认。\n\n## 重点分析\n- 人工成本差异需核查。\n\n## 改进建议\n1. 优化原材料采购策略\n2. 复核人工效率"


fake_client.llm_client = types.SimpleNamespace(chat=fake_chat)
fake_prompts = types.ModuleType("llm.prompts")
fake_prompts.BENCHMARK_ATTRIBUTION_PROMPT = "数据:{context}\n知识:{rag_context}"
fake_prompts.SYSTEM_ROLE = "测试角色"
old_modules = {name: sys.modules.get(name) for name in ("rag", "rag.retriever", "llm", "llm.client", "llm.prompts")}
sys.modules.update({
    "rag": fake_rag_pkg,
    "rag.retriever": fake_rag,
    "llm": fake_llm_pkg,
    "llm.client": fake_client,
    "llm.prompts": fake_prompts,
})
with tempfile.TemporaryDirectory() as temp_dir, patch.object(
    benchmark_module, "_ATTRIBUTION_CACHE_PATH", Path(temp_dir) / "benchmark_attribution_cache.json"
):
    try:
        attribution = asyncio.run(benchmark_attribution("板蓝根颗粒", "2026-03"))
        cached_attribution = asyncio.run(benchmark_attribution("板蓝根颗粒", "2026-03"))
    finally:
        for name, module in old_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

assert "1. 结论摘要" in attribution["attribution"]
assert "3. 改进建议" in attribution["attribution"]
assert "##" not in attribution["attribution"]
assert attribution["suggestions"]
assert {"suggestion", "department", "priority", "deadline"} <= set(attribution["suggestions"][0])
assert attribution["rag_sources"] == ["工艺文档"]
assert cached_attribution["cached"] is True
assert call_count["value"] == 1


# RPA 派发应消费对标归因上下文，并保留来源字段。
import rpa.client as rpa_client
from rpa.task_store import TaskStore


async def _benchmark_task_generation_and_dispatch():
    generated_items = [
        {"task_title": "核查原材料采购价格", "priority": "high"},
        {"task_title": "复核生产工时效率", "priority": "medium"},
    ]
    with patch.object(rpa_client, "_generate_llm_tasks", new_callable=AsyncMock, return_value=generated_items), patch.object(
        rpa_client, "send_rpa_task", new_callable=AsyncMock, return_value={"status": "accepted"}
    ), patch.object(rpa_client, "send_wechat_notify", new_callable=AsyncMock, return_value={"status": "sent"}):
        drafts = await rpa_client.generate_task_drafts(
            "板蓝根颗粒", "2026-03", "benchmark_attribution", attribution["attribution"],
            {"benchmark_differences": attribution["diff_data"]["rows"]},
        )
        assert all(task["source"]["analysis_scenario"] == "成本对标归因分析" for task in drafts["tasks"])
        saved = await rpa_client.save_selected_task_drafts(drafts["tasks"])
        assert saved["tasks_saved"] == 2
        dispatch_result = await rpa_client.dispatch_selected_tasks([task["task_id"] for task in saved["tasks"]])
    return dispatch_result


original_store = rpa_client.TASK_STORE
with tempfile.TemporaryDirectory() as directory:
    rpa_client.TASK_STORE = TaskStore(Path(directory) / "tasks.sqlite3")
    try:
        dispatch_result = asyncio.run(_benchmark_task_generation_and_dispatch())
    finally:
        rpa_client.TASK_STORE = original_store
assert dispatch_result["tasks_dispatched"] == 2
assert all(item["status"] == "sent" for item in dispatch_result["results"])
print("TC-BENCHMARK: 结构树、归因和结构化建议全部通过")
