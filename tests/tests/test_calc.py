"""TC-CALC-001/002/003: 计算模块测试"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))

from data.cost_data import cost_service
from analysis.dashboard import three_dim_compare, cost_trend, cost_structure, cost_waterfall, labor_metrics, material_detail_table

print("=" * 60)
print("TC-CALC: 计算模块测试")
print("=" * 60)

cost_service.load_all()

# TC-CALC-001: 三维对比
print("\n--- TC-CALC-001: 三维对比 ---")
result = three_dim_compare('银黄口服液', '2026-05')
assert 'rows' in result, "三维对比缺少rows"
assert len(result['rows']) == 6, f"应有6行, 实际{len(result['rows'])}行"
for row in result['rows']:
    assert 'metric' in row
    assert 'current' in row
    assert 'mom_change' in row
    print(f"  {row['metric']}: 本月={row['current']} 环比={row['mom_change']}% 预算偏差={row['budget_deviation']}%")

alerts = result.get('alerts', [])
print(f"  告警数: {len(alerts)}")
for a in alerts:
    print(f"    {a['message']}")

# TC-CALC-002: 人工工时指标
print("\n--- TC-CALC-002: 人工工时指标 ---")
labor = labor_metrics('银黄口服液', '2026-05')
assert 'metrics' in labor, "人工指标缺少metrics"
for key, val in labor['metrics'].items():
    print(f"  {key}: 当前={val['current']} 上月={val['prev']} 环比={val['change']}%")

# TC-CALC-003: 对标分析
print("\n--- TC-CALC-003: 对标差异 ---")
from analysis.benchmark import benchmark_diff, benchmark_breakdown
diff = benchmark_diff('板蓝根颗粒', '2026-03')
assert 'rows' in diff, "对标差异缺少rows"
expected = {
    '直接材料': (4.34, 4.56, -0.22, -4.82),
    '直接人工': (1.02, 1.15, -0.13, -11.30),
    '制造费用': (1.60, 1.77, -0.17, -9.60),
    '单位成本': (6.96, 7.48, -0.52, -6.95),
}
for row in diff['rows']:
    if row['dimension'] in expected:
        exp = expected[row['dimension']]
        assert abs(row['factory1'] - exp[0]) <= 0.01
        assert abs(row['factory2'] - exp[1]) <= 0.01
        assert abs(row['diff_amount'] - exp[2]) <= 0.01
        assert abs(row['diff_rate'] - exp[3]) <= 0.01
        assert row['direction'] == '一厂低'
    print(f"  {row['dimension']}: 一厂={row['factory1']} 二厂={row['factory2']} 差异={row['diff_amount']} ({row['direction']})")

breakdown = benchmark_breakdown('板蓝根颗粒', '2026-03')
assert 'breakdown' in breakdown, "结构拆解缺少breakdown"
assert abs(breakdown['total_diff'] - (-0.52)) <= 0.01
assert abs(sum(item['diff_amount'] for item in breakdown['breakdown']) - breakdown['total_diff']) <= 0.01
assert abs(sum(item['contribution'] for item in breakdown['breakdown']) - 100.0) <= 0.2
for b in breakdown['breakdown']:
    print(f"  {b['dimension']}: 差异={b['diff_amount']} 贡献度={b['contribution']}%")

# 趋势数据
print("\n--- 趋势数据 ---")
trend = cost_trend('银黄口服液')
assert len(trend['months']) == 6, f"应有6个月, 实际{len(trend['months'])}个"
print(f"  月份: {trend['months']}")
print(f"  单位成本: {trend['unit_cost']}")

# 成本结构
print("\n--- 成本结构 ---")
struct = cost_structure('银黄口服液', '2026-05')
for item in struct['data']:
    print(f"  {item['name']}: {item['value']}元 ({item['ratio']}%)")

# 瀑布图
print("\n--- 成本变动瀑布图 ---")
waterfall = cost_waterfall('银黄口服液', '2026-05')
assert waterfall['total_change'] == waterfall['items'][-1]['value']
factor_items = [item for item in waterfall['items'] if not item.get('is_total')]
assert all('contribution' in item and 'contribution_pct' in item for item in factor_items)
assert abs(sum(item['contribution'] for item in factor_items) - 100.0) <= 0.2
for item in waterfall['items']:
    print(f"  {item['name']}: {item['value']}")

# 原材料明细含环比
print("\n--- 原材料明细含环比 ---")
mat_detail = material_detail_table('银黄口服液', '2026-05')
for m in mat_detail['materials'][:3]:
    print(f"  {m['material_name']}: {m['unit_cost']} (环比: {m.get('mom_change', 'N/A')}%)")

print("\n" + "=" * 60)
print("TC-CALC: 全部通过!")
print("=" * 60)
