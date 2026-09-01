"""TC-API: FastAPI启动和API测试"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'app'))

import uvicorn
from fastapi.testclient import TestClient

print("=" * 60)
print("TC-API: FastAPI API测试")
print("=" * 60)

# 导入app并手动加载数据
from data.cost_data import cost_service
cost_service.load_all()

from main import app
client = TestClient(app)

# TC-API-001: 产品列表
print("\n--- TC-API-001: /api/products ---")
resp = client.get("/api/products")
assert resp.status_code == 200, f"状态码错误: {resp.status_code}"
data = resp.json()
assert 'products' in data
assert 'months' in data
assert len(data['products']) == 3
print(f"[PASS] 产品列表: {len(data['products'])}个")

# TC-API-002: 三维对比
print("\n--- TC-API-002: /api/dashboard/three-dim ---")
resp = client.get("/api/dashboard/three-dim?product=银黄口服液&month=2026-05")
assert resp.status_code == 200
data = resp.json()
assert 'rows' in data
print(f"[PASS] 三维对比: {len(data['rows'])}行")

# TC-API-003: 趋势数据
print("\n--- TC-API-003: /api/dashboard/trend ---")
resp = client.get("/api/dashboard/trend?product=银黄口服液")
assert resp.status_code == 200
data = resp.json()
assert len(data['months']) == 6
print(f"[PASS] 趋势数据: {len(data['months'])}个月")

# TC-API-003b: 多产品热力图
print("\n--- TC-API-003b: /api/dashboard/heatmap ---")
resp = client.get("/api/dashboard/heatmap")
assert resp.status_code == 200
data = resp.json()
assert len(data['products']) == 3
assert len(data['months']) == 6
assert set(data['values']) == {'unit_cost', 'material_cost', 'labor_cost', 'overhead_cost'}
assert all(len(values) == 18 for values in data['values'].values())
print(f"[PASS] 多产品热力图: {len(data['products'])}个产品×{len(data['months'])}个月")

# TC-API-004: 原材料明细
print("\n--- TC-API-004: /api/dashboard/material-detail ---")
resp = client.get("/api/dashboard/material-detail?product=银黄口服液&month=2026-05")
assert resp.status_code == 200
data = resp.json()
assert 'materials' in data
print(f"[PASS] 原材料明细: {len(data['materials'])}种")

# TC-API-005: 制造费用明细
print("\n--- TC-API-005: /api/dashboard/overhead-detail ---")
resp = client.get("/api/dashboard/overhead-detail?product=银黄口服液&month=2026-05")
assert resp.status_code == 200
data = resp.json()
assert 'overheads' in data
print(f"[PASS] 制造费用明细: {len(data['overheads'])}类")

# TC-API-006: 人工工时
print("\n--- TC-API-006: /api/dashboard/labor-metrics ---")
resp = client.get("/api/dashboard/labor-metrics?product=银黄口服液&month=2026-05")
assert resp.status_code == 200
data = resp.json()
assert 'metrics' in data
print(f"[PASS] 人工工时: 4个指标")

# TC-API-007: 成本结构
print("\n--- TC-API-007: /api/dashboard/structure ---")
resp = client.get("/api/dashboard/structure?product=银黄口服液&month=2026-05")
assert resp.status_code == 200
data = resp.json()
assert 'data' in data
print(f"[PASS] 成本结构: {len(data['data'])}项")

# TC-API-008: 瀑布图
print("\n--- TC-API-008: /api/dashboard/waterfall ---")
resp = client.get("/api/dashboard/waterfall?product=银黄口服液&month=2026-05")
assert resp.status_code == 200
data = resp.json()
assert 'items' in data
assert 'contribution' in data['items'][0]
print(f"[PASS] 瀑布图: {len(data['items'])}项")

# TC-API-008b: 看板告警/归因
print("\n--- TC-API-008b: /api/dashboard/attribution ---")
resp = client.get("/api/dashboard/attribution?product=银黄口服液&month=2026-06")
assert resp.status_code == 200
data = resp.json()
assert 'analysis' in data and data['analysis']
assert 'alerts' in data
assert data['重点分析'] is True
assert 'dashboard' in data['data'] and 'waterfall' in data['data']
print(f"[PASS] 看板归因: 告警{len(data['alerts'])}项")

# TC-API-009: 行业基准
print("\n--- TC-API-009: /api/dashboard/industry-bench ---")
resp = client.get("/api/dashboard/industry-bench?product=银黄口服液")
assert resp.status_code == 200
data = resp.json()
assert 'benchmarks' in data
print(f"[PASS] 行业基准: {len(data['benchmarks'])}条")

# TC-API-010: 对标差异
print("\n--- TC-API-010: /api/benchmark/diff ---")
resp = client.get("/api/benchmark/diff?product=板蓝根颗粒&month=2026-03")
assert resp.status_code == 200
data = resp.json()
assert 'rows' in data
print(f"[PASS] 对标差异: {len(data['rows'])}行")

# TC-API-011: 对标结构拆解
print("\n--- TC-API-011: /api/benchmark/breakdown ---")
resp = client.get("/api/benchmark/breakdown?product=板蓝根颗粒&month=2026-03")
assert resp.status_code == 200
data = resp.json()
assert 'breakdown' in data
print(f"[PASS] 结构拆解: {len(data['breakdown'])}项")

# TC-API-011b: 对标结构树
print("\n--- TC-API-011b: /api/benchmark/structure ---")
resp = client.get("/api/benchmark/structure?product=板蓝根颗粒&month=2026-03")
assert resp.status_code == 200
data = resp.json()
assert 'tree' in data and data['tree'].get('children')
material = next(n for n in data['tree']['children'] if n['name'] == '直接材料')
assert material.get('children')
print(f"[PASS] 结构树: {len(material['children'])}种原材料")

# TC-API-012: 成本预测
print("\n--- TC-API-012: /api/dashboard/forecast ---")
resp = client.get("/api/dashboard/forecast?product=银黄口服液")
assert resp.status_code == 200
data = resp.json()
assert 'forecasts' in data
print(f"[PASS] 成本预测: {len(data['forecasts'])}项指标")

# TC-API-013: 缺少参数
print("\n--- TC-API-013: 缺少参数 ---")
resp = client.get("/api/dashboard/three-dim?product=银黄口服液")
assert resp.status_code == 422
print(f"[PASS] 缺少参数返回422")

print("\n" + "=" * 60)
print("TC-API: 全部通过! (14/14)")
print("=" * 60)
