"""TC-DATA-001: 数据加载完整性测试"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))

from data.cost_data import cost_service

print("=" * 60)
print("TC-DATA-001: 数据加载完整性测试")
print("=" * 60)

# Step 1: 加载数据
cost_service.load_all()
print("[PASS] 数据加载成功")

# Step 2: 产品列表
products = cost_service.get_products()
months = cost_service.get_months()
print(f"[PASS] 产品列表: {len(products)}个 - {[p['name'] for p in products]}")
print(f"[PASS] 月份列表: {len(months)}个")

# Step 3: 成本汇总查询
summary = cost_service.get_cost_summary('银黄口服液', '2026-05')
assert summary is not None, "银黄口服液 2026-05 数据为空"
print(f"[PASS] 成本汇总查询: 单位成本={summary['unit_cost']}元/盒")
print(f"       材料={summary['material_cost']} 人工={summary['labor_cost']} 制造={summary['overhead_cost']}")

# Step 4: 原材料明细
materials = cost_service.get_material_detail('银黄口服液', '2026-05')
assert len(materials) > 0, "原材料明细为空"
print(f"[PASS] 原材料明细: {len(materials)}种")
for m in materials[:3]:
    print(f"       {m['material_name']}: {m['unit_cost']}元/盒 ({m['ratio']}%)")

# Step 5: 制造费用明细
overheads = cost_service.get_overhead_detail('银黄口服液', '2026-05')
assert len(overheads) > 0, "制造费用明细为空"
print(f"[PASS] 制造费用明细: {len(overheads)}类")
for o in overheads:
    print(f"       {o['category']}: {o['unit_cost']}元/盒")

# Step 6: 人工工时
labor = cost_service.get_labor_detail('银黄口服液', '2026-05')
assert labor is not None, "人工工时数据为空"
print(f"[PASS] 人工工时: 产量={labor['production']} 总工时={labor['total_hours']} 人数={labor['worker_count']}")

# Step 7: 预算数据
budget = cost_service.get_budget('银黄口服液', '2026-05')
assert budget is not None, "预算数据为空"
print(f"[PASS] 预算数据: 预算产量={budget['budget_production']} 预算单位成本={budget['budget_unit_cost']}")

# Step 8: 对标数据
bench = cost_service.get_benchmark('银黄口服液', '2026-05')
assert bench is not None, "对标数据为空"
print(f"[PASS] 对标数据(二厂): 单位成本={bench['unit_cost']}")

# Step 9: 市场行情
market = cost_service.get_market_prices()
assert len(market) > 0, "市场行情为空"
print(f"[PASS] 市场行情: {len(market)}种药材")

# Step 10: 行业基准
industry = cost_service.get_industry_benchmarks()
assert len(industry) > 0, "行业基准为空"
print(f"[PASS] 行业基准: {len(industry)}条")

# Step 11: 无效查询
invalid = cost_service.get_cost_summary('无效产品', '2026-05')
assert invalid is None, "无效产品应返回None"
print("[PASS] 无效查询返回None")

# Step 12: 边界 - 第一个月无上月
prev = cost_service.get_cost_summary_prev_month('银黄口服液', '2026-01')
assert prev is None, "第一个月应无上月数据"
print("[PASS] 边界: 第一个月无上月数据")

print("=" * 60)
print("TC-DATA-001: 全部通过!")
print("=" * 60)
