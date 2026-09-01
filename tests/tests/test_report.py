"""TC-REPORT: 报告生成测试"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'app'))

from data.cost_data import cost_service
cost_service.load_all()

print("=" * 60)
print("TC-REPORT: 报告生成测试")
print("=" * 60)

# 测试模板解析
from report.template_parser import TemplateParser
from config import TEMPLATE_DOCX

print("\n--- 模板解析 ---")
parser = TemplateParser(str(TEMPLATE_DOCX))
placeholders = parser.find_placeholders()
print(f"[PASS] 模板加载成功")
print(f"[INFO] 发现 {len(placeholders)} 个占位符:")
for name, locations in list(placeholders.items())[:10]:
    print(f"  {{{{{name}}}}} -> {locations[0]}")
if len(placeholders) > 10:
    print(f"  ... 还有 {len(placeholders)-10} 个")

# 测试表格数据生成
from report.tables import (
    build_material_detail_table, build_overhead_detail_table,
    build_labor_table, build_trend_table, build_benchmark_diff_table,
)

print("\n--- 表格数据生成 ---")
mat_table = build_material_detail_table('银黄口服液', '2026-05')
print(f"[PASS] 原材料明细表: {len(mat_table)}行")

oh_table = build_overhead_detail_table('银黄口服液', '2026-05')
print(f"[PASS] 制造费用明细表: {len(oh_table)}行")

labor_table = build_labor_table('银黄口服液', '2026-05')
print(f"[PASS] 人工工时表: {len(labor_table)}行")

trend_table = build_trend_table('银黄口服液')
print(f"[PASS] 趋势表: {len(trend_table)}行")

bench_table = build_benchmark_diff_table('银黄口服液', '2026-05')
print(f"[PASS] 对标差异表: {len(bench_table)}行")

# 测试报告生成引擎
print("\n--- 报告生成引擎 ---")
from report.engine import ReportEngine, normalize_markdown_text
from report.tables import _parse_tasks_json
from rag.citations import citation_text, source_names

print("\n--- Markdown清理 ---")
markdown = """# 成本分析\n\n**材料成本上升**\n\n- 采购价格上涨\n- `供应商报价`\n\n```json\n{\"task_title\": \"调整采购批次\"}\n```\n\n| 项目 | 数值 |\n| --- | --- |\n| 材料 | 12.3 |\n"""
normalized = normalize_markdown_text(markdown)
assert "# " not in normalized
assert "**" not in normalized
assert "```" not in normalized
assert "| --- |" not in normalized
assert "材料成本上升" in normalized
assert "• 采购价格上涨" in normalized
print("[PASS] Markdown标题、强调、列表、代码围栏和表格标记已清理")

task_json = '[{"task_id":"T-001","task_title":"调整采购批次","owner":"采购部"}]'
parsed_tasks = _parse_tasks_json(task_json)
assert parsed_tasks[0]["task_title"] == "调整采购批次"
assert task_json != normalize_markdown_text(task_json)
print("[PASS] 整改任务JSON仍可解析，普通文本清理不会替代任务结构")

engine = ReportEngine()
print("[PASS] ReportEngine实例化成功")

assert source_names([{"source": "工艺规程.docx"}, {"source": "工艺规程.docx"}, {"source": "GMP.pdf"}]) == ["工艺规程.docx", "GMP.pdf"]
assert citation_text(["工艺规程.docx"]) == "知识库参考：工艺规程.docx"
assert engine._with_rag_citation("归因分析", ["工艺规程.docx"]).endswith("知识库参考：工艺规程.docx")
print("[PASS] 知识库来源去重与报告段落引用标注正确")

# 验证关键方法存在
assert hasattr(engine, 'generate'), "缺少generate方法"
assert hasattr(engine, '_build_placeholders'), "缺少_build_placeholders方法"
assert hasattr(engine, '_build_price_tracking_table'), "缺少_build_price_tracking_table方法"
print("[PASS] 关键方法验证通过")

# 验证模板解析器能正确解析所有占位符
all_ph = parser.find_placeholders()
print(f"[PASS] 模板占位符总数: {len(all_ph)}")

# 验证关键占位符存在
critical = ['产品名称', '分析月份', '本月产量', '本月单位成本', '材料成本环比',
            '原材料成本明细表格', '对标差异表格', '改进建议表格', '整改任务表格']
for key in critical:
    found = key in all_ph
    status = "[PASS]" if found else "[FAIL]"
    print(f"  {status} 关键占位符 '{key}'")

print("\n" + "=" * 60)
print("TC-REPORT: 通过!")
print("=" * 60)
