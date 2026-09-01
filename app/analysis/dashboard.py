"""看板数据计算模块 — 三维对比/趋势/结构/瀑布图"""
import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.cost_data import cost_service
from config import ALERT_THRESHOLD, MONTHS, PRODUCTS

# 归因结果按产品和月份缓存，避免同一看板参数重复调用模型。
_ATTRIBUTION_CACHE: dict[tuple[str, str], dict] = {}
_ATTRIBUTION_CACHE_LOCKS: dict[tuple[str, str], threading.Lock] = {}
_ATTRIBUTION_LOCKS_GUARD = threading.Lock()


def _attribution_lock(cache_key: tuple[str, str]) -> threading.Lock:
    """为每个产品/月提供独立锁，避免并发请求重复调用模型。"""
    with _ATTRIBUTION_LOCKS_GUARD:
        return _ATTRIBUTION_CACHE_LOCKS.setdefault(cache_key, threading.Lock())


def three_dim_compare(product: str, month: str) -> dict:
    """三维对比: 本月/上月/环比/去年同月/同比/预算/预算偏差"""
    current = cost_service.get_cost_summary(product, month)
    prev = cost_service.get_cost_summary_prev_month(product, month)
    last_year = cost_service.get_cost_summary_last_year(product, month)
    budget = cost_service.get_budget(product, month)

    if not current:
        return {"error": f"未找到数据: {product} {month}"}

    def _row(label, key, is_cost=True):
        cur_val = current[key] if current else 0
        prev_val = prev[key] if prev else 0
        ly_val = last_year[key] if last_year else 0
        bud_val = budget.get(f"budget_{key}" if f"budget_{key}" in (budget or {}) else key, 0) if budget else 0

        # 对于预算，映射正确的字段名
        if budget:
            budget_map = {
                'production': 'budget_production',
                'material_cost': 'budget_material',
                'labor_cost': 'budget_labor',
                'overhead_cost': 'budget_overhead',
                'unit_cost': 'budget_unit_cost',
                'total_cost': 'budget_total_cost',
            }
            bud_val = budget.get(budget_map.get(key, key), 0)

        mom = cost_service.calc_mom_change(cur_val, prev_val) if prev_val else None
        yoy = cost_service.calc_mom_change(cur_val, ly_val) if ly_val else None
        bud_dev = cost_service.calc_budget_deviation(cur_val, bud_val) if bud_val else None

        return {
            'metric': label,
            'current': round(cur_val, 2),
            'last_month': round(prev_val, 2) if prev_val else None,
            'mom_change': mom,
            'last_year': round(ly_val, 2) if ly_val else None,
            'yoy_change': yoy,
            'budget': round(bud_val, 2) if bud_val else None,
            'budget_deviation': bud_dev,
        }

    rows = [
        _row('产量(盒)', 'production', False),
        _row('单位成本(元/盒)', 'unit_cost'),
        _row('总成本(元)', 'total_cost'),
        _row('直接材料(元/盒)', 'material_cost'),
        _row('直接人工(元/盒)', 'labor_cost'),
        _row('制造费用(元/盒)', 'overhead_cost'),
    ]

    # 波动告警
    alerts = []
    for r in rows:
        if r['mom_change'] is not None and abs(r['mom_change']) >= ALERT_THRESHOLD:
            direction = "↑" if r['mom_change'] > 0 else "↓"
            alerts.append({
                'metric': r['metric'],
                'change': r['mom_change'],
                'direction': direction,
                'message': f"{r['metric']} 环比{direction}{abs(r['mom_change'])}%"
            })

    return {
        'product': product,
        'month': month,
        'spec': current['spec'],
        'production': current['production'],
        'rows': rows,
        'alerts': alerts,
    }


def cost_trend(product: str) -> dict:
    """近6个月成本趋势数据"""
    trend = cost_service.get_cost_trend(product)
    return {
        'product': product,
        'months': [t['month'] for t in trend],
        'material': [t['material'] for t in trend],
        'labor': [t['labor'] for t in trend],
        'overhead': [t['overhead'] for t in trend],
        'unit_cost': [t['unit_cost'] for t in trend],
        'production': [t['production'] for t in trend],
    }


def cost_heatmap(products: list[str] | None = None) -> dict:
    """产品×月份×成本要素热力图数据（单位成本口径，元/盒）。"""
    selected_products = [p for p in (products or PRODUCTS) if p in PRODUCTS]
    if not selected_products:
        selected_products = list(PRODUCTS)

    elements = {
        'unit_cost': '单位成本',
        'material_cost': '直接材料',
        'labor_cost': '直接人工',
        'overhead_cost': '制造费用',
    }
    values = {key: [] for key in elements}
    for product_idx, product in enumerate(selected_products):
        for month_idx, month in enumerate(MONTHS):
            summary = cost_service.get_cost_summary(product, month) or {}
            for key in elements:
                value = summary.get(key)
                values[key].append([month_idx, product_idx, round(float(value), 2)] if value is not None else [month_idx, product_idx, None])

    return {
        'products': selected_products,
        'months': list(MONTHS),
        'elements': elements,
        'values': values,
    }


def cost_structure(product: str, month: str) -> dict:
    """成本结构饼图数据"""
    current = cost_service.get_cost_summary(product, month)
    if not current:
        return {"error": "未找到数据"}
    total = current['material_cost'] + current['labor_cost'] + current['overhead_cost']
    if total <= 0:
        return {"product": product, "month": month, "data": []}
    return {
        'product': product,
        'month': month,
        'data': [
            {'name': '直接材料', 'value': round(current['material_cost'], 2),
             'ratio': round(current['material_cost'] / total * 100, 1)},
            {'name': '直接人工', 'value': round(current['labor_cost'], 2),
             'ratio': round(current['labor_cost'] / total * 100, 1)},
            {'name': '制造费用', 'value': round(current['overhead_cost'], 2),
             'ratio': round(current['overhead_cost'] / total * 100, 1)},
        ]
    }


def cost_waterfall(product: str, month: str) -> dict:
    """成本变动瀑布图: 各要素对总成本变动的贡献"""
    current = cost_service.get_cost_summary(product, month)
    prev = cost_service.get_cost_summary_prev_month(product, month)
    if not current or not prev:
        return {"error": "缺少数据"}

    mat_diff = round(current['material_cost'] - prev['material_cost'], 2)
    lab_diff = round(current['labor_cost'] - prev['labor_cost'], 2)
    oh_diff = round(current['overhead_cost'] - prev['overhead_cost'], 2)
    total_diff = round(mat_diff + lab_diff + oh_diff, 2)
    factors = [
        ('直接材料变动', mat_diff),
        ('直接人工变动', lab_diff),
        ('制造费用变动', oh_diff),
    ]
    items = []
    for name, value in factors:
        contribution = round(value / total_diff * 100, 1) if total_diff else None
        items.append({
            'name': name,
            'value': value,
            # Keep both names for API consumers: contribution is the
            # business term used by the requirement, while contribution_pct
            # remains explicit for chart formatting.
            'contribution': contribution,
            'contribution_pct': contribution,
        })
    items.append({
        'name': '单位成本变动',
        'value': total_diff,
        'contribution': 100.0 if total_diff else None,
        'contribution_pct': 100.0 if total_diff else None,
        'is_total': True,
    })

    return {
        'product': product,
        'month': month,
        'base': round(prev['unit_cost'], 2),
        'current': round(current['unit_cost'], 2),
        'total_change': total_diff,
        'items': items,
    }


def dashboard_attribution(product: str, month: str, force: bool = False) -> dict:
    cache_key = (product, month)
    data = three_dim_compare(product, month)
    if data.get('error'):
        return data
    waterfall = cost_waterfall(product, month)
    material = material_detail_table(product, month)
    context = {
        'dashboard': data,
        'waterfall': waterfall,
        'material_detail': material,
    }
    fallback = _build_attribution_fallback(data, waterfall, material)
    cached = None if force else _ATTRIBUTION_CACHE.get(cache_key)
    if cached:
        return {
            'product': product,
            'month': month,
            'data': context,
            'alerts': data.get('alerts', []),
            '重点分析': bool(data.get('alerts')),
            'analysis': cached['analysis'] if cached.get('rag_sources') else _append_data_only_notice(cached['analysis']),
            'analysis_source': cached.get('source', 'ai'),
            'rag_sources': cached.get('rag_sources', []),
            'rag_used': bool(cached.get('rag_sources')),
            'knowledge_base_version': 'knowledge_index_meta',
            'cached': True,
        }

    # 同一产品/月的并发请求共享一次模型调用；不同产品/月互不阻塞。
    with _attribution_lock(cache_key):
        cached = None if force else _ATTRIBUTION_CACHE.get(cache_key)
        if cached:
            return {
                'product': product,
                'month': month,
                'data': context,
                'alerts': data.get('alerts', []),
                '重点分析': bool(data.get('alerts')),
                'analysis': cached['analysis'] if cached.get('rag_sources') else _append_data_only_notice(cached['analysis']),
                'analysis_source': cached.get('source', 'ai'),
                'rag_sources': cached.get('rag_sources', []),
                'rag_used': bool(cached.get('rag_sources')),
                'knowledge_base_version': 'knowledge_index_meta',
                'cached': True,
            }
        rag_results = _dashboard_rag_results(product, month, data, material) if data.get('alerts') else []
        rag_sources = _rag_source_names(rag_results)
        rag_context = '\n'.join(
            f"[{index + 1}] [来源：{result.get('source', '知识库')}] {result.get('content', '')[:500]}"
            for index, result in enumerate(rag_results)
        )
        source = 'fallback'
        try:
            from llm.client import llm_client
            from llm.prompts import DASHBOARD_ATTRIBUTION_PROMPT, SYSTEM_ROLE
            text = llm_client.chat(
                DASHBOARD_ATTRIBUTION_PROMPT.format(
                    product=product,
                    month=month,
                    context=json.dumps(context, ensure_ascii=False, indent=2),
                    rag_context=rag_context or '本次无阈值告警，不使用知识库。',
                ),
                system=SYSTEM_ROLE,
                max_tokens=1800,
            )
            if text and str(text).strip():
                source = 'ai'
        except Exception:
            text = fallback
        text = text or fallback
        used_rag_sources = rag_sources if source == 'ai' else []
        if used_rag_sources:
            text = _append_rag_sources(text, used_rag_sources)
        else:
            text = _append_data_only_notice(text)
        # 仅缓存模型生成结果；临时网络/超时兜底文本不应污染后续切换结果。
        if source == 'ai':
            _ATTRIBUTION_CACHE[cache_key] = {'analysis': text, 'source': source, 'rag_sources': used_rag_sources}
    return {
        'product': product,
        'month': month,
        'data': context,
        'alerts': data.get('alerts', []),
        '重点分析': bool(data.get('alerts')),
        'analysis': text,
        'analysis_source': source,
        'rag_sources': used_rag_sources,
        'rag_used': bool(used_rag_sources),
        'knowledge_base_version': 'knowledge_index_meta',
        'cached': False,
    }


def _dashboard_rag_results(product: str, month: str, data: dict, material: dict) -> list[dict]:
    """仅针对阈值告警检索归因依据，避免普通分析增加延迟。"""
    try:
        from rag.retriever import hybrid_search
        metrics = '；'.join(
            f"{alert.get('metric', '')} 环比{alert.get('change', '')}%"
            for alert in data.get('alerts', [])
        )
        material_names = '、'.join(
            str(item.get('material_name', ''))
            for item in (material.get('materials') or [])[:3]
            if item.get('material_name')
        )
        query = f"{product} {month} 成本归因 {metrics} {material_names} 采购价格 单耗 提取工艺 设备 生产日历"
        return hybrid_search(query, top_k=3)
    except Exception:
        return []


def _rag_source_names(results: list[dict] | None) -> list[str]:
    """读取知识库来源名称，兼容 RAG 模块未初始化的情况。"""
    try:
        from rag.citations import source_names
        return source_names(results)
    except Exception:
        names = []
        for result in results or []:
            source = str(result.get('source') or '').strip()
            if source and source not in names:
                names.append(source)
        return names


def _append_rag_sources(text: str, sources: list[str]) -> str:
    """强制在报告文本中保留实际使用的知识库来源。"""
    marker = '## 知识库来源'
    if marker in text:
        return text
    lines = '\n'.join(f"- {source}" for source in sources)
    return f"{text.rstrip()}\n\n{marker}\n\n{lines}"


def _append_data_only_notice(text: str) -> str:
    marker = "本结论仅基于成本数据"
    return text if marker in text else f"{text.rstrip()}\n\n> {marker}。"


def _build_attribution_fallback(data: dict, waterfall: dict, material: dict) -> str:
    rows = {row['metric']: row for row in data.get('rows', [])}
    total = waterfall.get('total_change', 0)
    direction = '上涨' if total > 0 else ('下降' if total < 0 else '基本持平')
    unit = rows.get('单位成本(元/盒)', {})
    mom_change = unit.get('mom_change')
    mom_text = f"{mom_change:+.2f}%" if mom_change is not None else "暂无环比数据"
    parts = [
        "## 结论摘要",
        f"{data['product']} {data['month']}单位成本环比{direction}{abs(total):.2f}元/盒（环比{mom_text}）。",
    ]
    factors = [item for item in waterfall.get('items', []) if not item.get('is_total')]
    factors.sort(key=lambda item: abs(item.get('value', 0)), reverse=True)
    parts.append("## 重点分析" if data.get('alerts') else "## 常规分析")
    for item in factors:
        pct = item.get('contribution_pct')
        pct_text = f"贡献总变动的{pct:.1f}%" if pct is not None else "贡献度无法计算"
        parts.append(f"- **{item['name'].replace('变动', '')}**{'上涨' if item['value'] > 0 else '下降'}{abs(item['value']):.2f}元/盒（{pct_text}）。")
    top_material = (material.get('materials') or [None])[0]
    if top_material:
        material_mom = top_material.get('mom_change')
        material_mom_text = f"{material_mom:+.2f}%" if material_mom is not None else "暂无环比数据"
        parts.append(f"明细显示，**{top_material.get('material_name', '主要原材料')}**单位成本为{top_material.get('unit_cost', 0):.4f}元/盒，环比{material_mom_text}。")
    parts.extend([
        "## 改进建议",
        "1. 采购部门核查主要原材料采购价格、合同调价条款及供应商报价变动。",
        "2. 生产部门复核原材料单耗、提取收率和近期工艺参数；设备/财务部门同步确认能耗与产量摊薄影响。",
    ])
    return '\n\n'.join(parts)


def labor_metrics(product: str, month: str) -> dict:
    """人工工时4个派生指标"""
    current = cost_service.get_labor_detail(product, month)
    prev = cost_service.get_labor_detail_prev(product, month)

    if not current:
        return {"error": "未找到人工工时数据"}

    def _calc(d):
        if not d:
            return None
        prod = d['production']
        cost = d['total_labor_cost']
        hours = d['total_hours']
        workers = d['worker_count']
        days = d['work_days']
        return {
            'unit_labor_cost': round(cost / prod, 4) if prod else 0,
            'labor_hours_per_10k': round(hours / prod * 10000, 2) if prod else 0,
            'avg_hourly_wage': round(cost / hours, 2) if hours else 0,
            'labor_efficiency': round(prod / (workers * days), 1) if workers * days else 0,
            'production': prod,
            'total_labor_cost': cost,
            'total_hours': hours,
            'worker_count': workers,
            'work_days': days,
        }

    cur_metrics = _calc(current)
    prev_metrics = _calc(prev)

    def _with_change(cur_key, prev_key=None):
        prev_key = prev_key or cur_key
        cur_val = cur_metrics.get(cur_key, 0) if cur_metrics else 0
        prev_val = prev_metrics.get(prev_key, 0) if prev_metrics else 0
        change = cost_service.calc_mom_change(cur_val, prev_val) if prev_val else None
        return {'current': cur_val, 'prev': prev_val, 'change': change}

    return {
        'product': product,
        'month': month,
        'metrics': {
            'unit_labor_cost': _with_change('unit_labor_cost'),
            'labor_hours_per_10k': _with_change('labor_hours_per_10k'),
            'avg_hourly_wage': _with_change('avg_hourly_wage'),
            'labor_efficiency': _with_change('labor_efficiency'),
        },
        'raw': cur_metrics,
    }


def material_detail_table(product: str, month: str) -> dict:
    """原材料明细表（含环比）"""
    current = cost_service.get_material_detail(product, month)
    prev_map = cost_service.get_material_detail_prev(product, month)

    for item in current:
        prev_cost = prev_map.get(item['material_name'], 0)
        item['prev_unit_cost'] = prev_cost
        item['mom_change'] = cost_service.calc_mom_change(item['unit_cost'], prev_cost) if prev_cost else None

    return {
        'product': product,
        'month': month,
        'materials': current,
    }


def overhead_detail_table(product: str, month: str) -> dict:
    """制造费用明细表（含环比）"""
    current = cost_service.get_overhead_detail(product, month)
    prev_map = cost_service.get_overhead_detail_prev(product, month)

    for item in current:
        prev_cost = prev_map.get(item['category'], 0)
        item['prev_unit_cost'] = prev_cost
        item['mom_change'] = cost_service.calc_mom_change(item['unit_cost'], prev_cost) if prev_cost else None

    return {
        'product': product,
        'month': month,
        'overheads': current,
    }


def industry_benchmark(product: str) -> dict:
    """行业基准数据"""
    category_map = {
        "银黄口服液": "口服液类",
        "板蓝根颗粒": "颗粒剂类",
        "六味地黄胶囊": "胶囊剂类",
    }
    category = category_map.get(product, "")
    benchmarks = cost_service.get_industry_benchmarks(category)
    overall = cost_service.get_industry_benchmarks("中成药行业整体")
    return {
        'product': product,
        'category': category,
        'benchmarks': benchmarks,
        'overall': overall,
    }
