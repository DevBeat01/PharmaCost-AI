"""看板数据计算模块 — 三维对比/趋势/结构/瀑布图"""
import json
import sys
import threading
import queue
import time
import logging
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.cost_data import cost_service
from config import ALERT_THRESHOLD

logger = logging.getLogger("analysis.dashboard")

_RAG_TIMEOUT_SECONDS = 4.0
# Some OpenAI-compatible models take over 45 seconds to produce the first
# token for the large dashboard context. Keep the server below the browser's
# 120-second SSE deadline, while retaining a finite failure boundary.
_LLM_STREAM_TIMEOUT_SECONDS = 100.0
# Keep enough room for the summary, multi-driver focus analysis and actionable
# recommendations in one response. The frontend renders the stream while it
# arrives, so a larger cap does not delay the first visible text.
_ATTRIBUTION_MAX_TOKENS = 4000
_FOCUS_METRIC_NAMES = ("单位成本", "总成本", "直接材料", "直接人工", "制造费用")

# 归因结果按产品和月份缓存，避免同一看板参数重复调用模型。
_ATTRIBUTION_CACHE: dict[tuple[str, str], dict] = {}
_ATTRIBUTION_CACHE_LOCKS: dict[tuple[str, str], threading.Lock] = {}
_ATTRIBUTION_LOCKS_GUARD = threading.Lock()


def _attribution_lock(cache_key: tuple[str, str]) -> threading.Lock:
    """为每个产品/月提供独立锁，避免并发请求重复调用模型。"""
    with _ATTRIBUTION_LOCKS_GUARD:
        return _ATTRIBUTION_CACHE_LOCKS.setdefault(cache_key, threading.Lock())


def _focus_alerts(data: dict) -> list[dict]:
    """Return threshold alerts that represent costs, excluding production-only changes."""
    return [
        alert for alert in data.get('alerts', [])
        if any(name in str(alert.get('metric', '')) for name in _FOCUS_METRIC_NAMES)
    ]


def _focus_analysis_section(data: dict, waterfall: dict, material: dict) -> str:
    """Build a non-repetitive, evidence-led focus analysis."""
    alerts = _focus_alerts(data)
    if not alerts:
        return ''
    rows = {row.get('metric'): row for row in data.get('rows', [])}
    factors = [item for item in waterfall.get('items', []) if not item.get('is_total')]
    factors.sort(key=lambda item: abs(float(item.get('value') or 0)), reverse=True)
    factor_map = {str(item.get('name', '')).replace('变动', ''): item for item in factors}
    def row_for(name):
        return rows.get(next((key for key in rows if str(key).startswith(name)), name), {})
    def pct(value):
        return f"{float(value):+.2f}%" if value is not None else "暂无"
    def money(value):
        return f"{float(value):.2f}"

    production = rows.get('产量(盒)', {})
    total = rows.get('总成本(元)', {})
    unit = rows.get('单位成本(元/盒)', {})
    lines = ["## 重点分析"]
    overview = []
    if unit.get('current') is not None:
        overview.append(f"单位成本为{money(unit['current'])}元/盒，较上月{money(unit.get('last_month', 0))}元/盒变化{money(float(unit['current']) - float(unit.get('last_month') or 0))}元/盒（环比{pct(unit.get('mom_change'))}）")
    if total.get('current') is not None and total.get('last_month') is not None:
        overview.append(f"总成本由{float(total['last_month']):,.0f}元变为{float(total['current']):,.0f}元，变化{float(total['current']) - float(total['last_month']):+,.0f}元")
    if production.get('current') is not None and production.get('last_month') is not None:
        overview.append(f"产量{float(production['current']):,.0f}盒（上月{float(production['last_month']):,.0f}盒，{pct(production.get('mom_change'))}）")
    lines.append("归因总览：" + "；".join(overview) + "。成本要素按对单位成本变动的贡献度排序，下面只写数据证据和对应待核查事项。")

    # Each driver gets one focused paragraph; no generic repeated disclaimer.
    for index, name in enumerate(("直接材料", "直接人工", "制造费用"), 1):
        row = row_for(name)
        item = factor_map.get(name, {})
        if not row and not item:
            continue
        contribution = item.get('contribution_pct')
        fact = f"本月为{money(row.get('current', 0))}，上月为{money(row.get('last_month', 0))}，变动{float(row.get('current', 0)) - float(row.get('last_month') or 0):+.2f}元/盒（环比{pct(row.get('mom_change'))}）"
        if contribution is not None:
            fact += f"，贡献单位成本变动{float(contribution):.1f}%"
        if row.get('budget') is not None:
            fact += f"；预算{money(row['budget'])}元/盒，预算偏差{pct(row.get('budget_deviation'))}"
        if name == '直接材料':
            materials = material.get('materials') or []
            top = materials[:3]
            detail = '；'.join(f"{m.get('material_name')} {money(m.get('unit_cost', 0))}元/盒（环比{pct(m.get('mom_change'))}，占比{float(m.get('ratio', 0)):.1f}%）" for m in top)
            evidence = f"明细中排名靠前的物料为：{detail}。" if detail else "当前没有可用的原材料明细。"
            check = "待核查采购入库价、合同单价、领料单、采购价格和单位消耗，判断价格变动还是单耗/收率变动。"
            action = "采购部会同生产部5个工作日内提交价格与单耗差异核查表及整改措施（附前三项物料明细）。"
            contribution_label = f"贡献{float(contribution):.1f}%" if contribution is not None else "贡献度待核算"
            judgment = f"材料是本次单位成本变化的首要驱动（{contribution_label}），明细显示主要物料同步变动；具体是采购价格还是单耗变化，需用批次记录验证。"
        elif name == '直接人工':
            try:
                labor = labor_metrics(data.get('product', ''), data.get('month', ''))
                raw = labor.get('raw') or {}
                evidence = f"人工原始记录：产量{raw.get('production', '暂无')}盒、总工时{raw.get('total_hours', '暂无')}小时、生产人数{raw.get('worker_count', '暂无')}人、工作天数{raw.get('work_days', '暂无')}天。"
            except Exception:
                evidence = "人工工时明细暂不可用。"
            check = "待核查工时、工资率、排班和人员配置，区分总工时变化与产量摊薄效应。"
            action = "生产部5个工作日内提交工时利用率、工资率和排班复核记录及效率改善方案。"
            judgment = "人工单位成本变动幅度较小，不能单独解释本次总成本波动；应重点判断工时、工资率和产量摊薄三者的贡献。"
        else:
            try:
                overheads = cost_service.get_overhead_detail(data.get('product', ''), data.get('month', ''))
            except Exception:
                overheads = []
            top = sorted(overheads, key=lambda x: float(x.get('unit_cost', 0)), reverse=True)[:3]
            detail = '；'.join(f"{o.get('category')} {money(o.get('unit_cost', 0))}元/盒" for o in top)
            evidence = f"制造费用明细排名靠前项目：{detail}。" if detail else "当前没有可用的制造费用明细。"
            check = "待核查能耗、维修、折旧、费用科目和分摊表，确认固定费用及产量规模效应。"
            action = "设备部会同财务部5个工作日内提交能耗、维修和分摊差异表。"
            judgment = "制造费用下降幅度低于产量降幅，提示固定费用分摊存在规模效应；能耗、维修或折旧的具体影响仍需明细验证。"
        heading = next((key for key in rows if str(key).startswith(name)), name)
        lines.append(f"### {index}. {heading}\n数据事实：{fact}。\n\n明细证据：{evidence}\n\n影响判断：{judgment}\n\n核查路径：{check}\n\n处置动作：{action}")

    if total.get('current') is not None:
        lines.append(f"### 总成本与产量影响\n总成本变化主要由产量从{float(production.get('last_month', 0)):,.0f}盒变为{float(production.get('current', 0)):,.0f}盒以及单位成本要素变动共同造成；不能将总成本下降直接等同于成本效率改善。财务部应将产量、单位成本和总成本按同一批次口径勾稽，形成一张总成本桥接表。")
    return '\n\n'.join(lines)


def _ensure_focus_analysis(text: str, data: dict, waterfall: dict, material: dict) -> str:
    """Normalize attribution into exactly: summary, analysis, suggestions, sources."""
    value = str(text or '').replace('\r', '').strip()
    chapter_names = r"结论摘要|总体结论|结论|重点分析|常规分析|差异分析|改进建议|建议|知识库依据|知识库来源"
    heading_re = re.compile(
        r"(?m)^\s*(?:(?:(?:#{1,6}\s*)|(?:(?:[一二三四五六七八九十百]+|\d+)[、.)．.]\s+))"
        r"(?P<key>" + chapter_names + r")\s*(?:(?:[:：]\s*(?P<inline>.*))|(?:[（(](?P<paren>.*)[）)]))?\s*$|"
        r"^\s*(" + chapter_names + r")\s*$|"
        r"^\s*(知识库依据|知识库来源)\s*[:：]\s*$)"
    )
    sections = {}
    matches = list(heading_re.finditer(value))
    unstructured_body = value if not matches else ''
    aliases = {'总体结论': '结论摘要', '结论': '结论摘要', '常规分析': '常规分析',
               '差异分析': '重点分析', '建议': '改进建议', '知识库来源': '知识库依据'}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(value)
        raw_key = next((group for group in match.groups() if group), '')
        key = aliases.get(raw_key, raw_key)
        inline = (match.groupdict().get('inline') or '').strip()
        body = value[match.end():end].strip()
        if inline:
            body = f"{inline}\n\n{body}".strip()
        # Keep the first occurrence of each top-level chapter. A repeated
        # chapter is model formatting noise and must not be shown twice.
        sections.setdefault(key, body)

    # Older prompts sometimes emitted this wrapper as a standalone heading.
    # It is not a report chapter and only makes the analysis look duplicated.
    for key in ('结论摘要', '重点分析', '常规分析', '改进建议', '知识库依据'):
        if key in sections:
            sections[key] = re.sub(r"(?m)^\s*数据核验与处置要点\s*$", "", sections[key]).strip()

    summary = sections.get('结论摘要') or sections.get('总体结论') or sections.get('结论') or ''
    suggestion = sections.get('改进建议') or sections.get('建议') or ''
    source_body = _compact_source_body(sections.get('知识库依据', ''))
    focus_model = sections.get('重点分析') or sections.get('常规分析') or sections.get('差异分析') or unstructured_body
    focus_section = _focus_analysis_section(data, waterfall, material)

    if _focus_alerts(data) and focus_section:
        # Keep the model's reasoning as the primary narrative. The rule-based
        # section is appended only as a factual supplement when the model gave
        # a short but meaningful explanation, and replaces pure filler only.
        model_body = _clean_focus_model_body(focus_model)
        if model_body and not _is_focus_placeholder(model_body):
            analysis = model_body
            if not _is_substantive_focus_model(model_body):
                generated = re.sub(r"^##\s*重点分析\s*", "", focus_section, count=1).strip()
                if generated and generated not in analysis:
                    analysis += f"\n\n数据补充：\n{generated}"
        else:
            analysis = re.sub(r"^##\s*重点分析\s*", "", focus_section, count=1).strip()
        analysis = analysis.strip()
    else:
        analysis = focus_model or "暂无成本要素波动分析。"

    if not summary:
        summary = f"{data.get('product', '')} {data.get('month', '')}成本数据已完成分析。"
    suggestion = _actionable_dashboard_suggestions(suggestion, data, waterfall)

    output = ["## 结论摘要", summary, f"## {'重点分析' if _focus_alerts(data) else '常规分析'}", analysis, "## 改进建议", suggestion]
    if source_body:
        output.extend(["## 知识库依据", source_body])
    return "\n\n".join(part for part in output if part).strip()


def _clean_focus_model_body(value: str) -> str:
    """Remove only structural noise while retaining the model's prose."""
    body = str(value or '').strip()
    body = re.sub(r"(?m)^\s*(?:#{1,6}\s*)?(?:重点分析|常规分析|差异分析)\s*[:：]?\s*$", "", body)
    body = re.sub(r"(?m)^\s*数据核验与处置要点\s*$", "", body)
    # A model occasionally echoes the next top-level chapter in the section
    # body when it omits a blank line; do not let that duplicate suggestions or
    # source evidence in the focus paragraph.
    body = re.split(r"(?m)^\s*(?:#{1,6}\s*)?(?:改进建议|建议|知识库依据|知识库来源)\s*[:：]?\s*$", body, maxsplit=1)[0]
    return body.strip()


def _is_substantive_focus_model(body: str) -> bool:
    """Detect detailed model reasoning instead of generic filler text."""
    if len(body) < 180:
        return False
    quantitative = len(re.findall(r"\d+(?:[,.，]\d+)*(?:%|％|元|盒|小时|天|次|项)", body))
    reasoning = len(re.findall(r"原因|由于|因此|表明|说明|驱动|贡献|结合|对比|可能|需进一步核查|建议|应当|应重点", body))
    generic = len(re.findall(r"具体业务原因需|结合业务情况持续分析|仅基于成本数据|不能直接证明", body))
    return quantitative >= 2 and reasoning >= 2 and generic < 4


def _is_focus_placeholder(body: str) -> bool:
    """识别没有实际归因内容的占位语，避免它覆盖规则分析。"""
    normalized = re.sub(r"\s+", "", str(body or ""))
    if not normalized:
        return True
    placeholders = (
        "分析很简单", "暂无成本要素波动分析", "请结合业务情况持续分析",
        "重点波动已告警，请进一步核查", "当前数据不足，无法分析",
    )
    return any(item in normalized for item in placeholders)


def _actionable_dashboard_suggestions(raw: str, data: dict, waterfall: dict) -> str:
    """Keep model advice visible and append executable details when needed."""
    meaningful = [line.strip() for line in re.split(r"\n+", str(raw or "")) if line.strip()]
    cleaned = re.sub(r"(?m)^\s*(?:#+\s*)?(?:\d+[.、]\s*)?改进建议\s*[:：]?\s*$", "", str(raw)).strip()
    # Keep the model's wording whenever it contains any meaningful advice.
    # Do not force the standard three-driver template just because an advice
    # item omits an owner, deliverable, or deadline; those details belong to
    # the model output and should not hide its actual recommendations.
    if meaningful and not _is_suggestion_placeholder(cleaned):
        return cleaned

    return _build_dashboard_tasks(data, waterfall)


def _is_suggestion_placeholder(value: str) -> bool:
    normalized = re.sub(r"\s+", "", str(value or ""))
    normalized = re.sub(r"^(?:\d+[.、)）]|[-*])", "", normalized)
    if not normalized:
        return True
    return normalized in {"持续关注成本。", "持续关注成本", "暂无。", "无。"} or bool(
        re.fullmatch(r"(?:建议|请)?持续关注(?:成本|相关指标)[。.!！]??", normalized)
    )


def _build_dashboard_tasks(data: dict, waterfall: dict) -> str:
    """Build the minimum executable supplement when model advice is incomplete."""

    factors = [item for item in waterfall.get('items', []) if not item.get('is_total')]
    factors.sort(key=lambda item: abs(float(item.get('value') or 0)), reverse=True)
    month = str(data.get('month') or '')
    deadline = f"{month}后5个工作日内" if month else "5个工作日内"
    tasks = []
    for item in factors:
        name = str(item.get('name') or '').replace('变动', '')
        if name == '直接材料':
            tasks.append(f"采购部核查主要药材采购入库价、合同单价、领料单和单位消耗，提交《直接材料价格与单耗差异表》，{deadline}完成。")
        elif name == '直接人工':
            tasks.append(f"生产部复核总工时、工资率、排班与产量摊薄，提交《人工效率复核记录及改善方案》，{deadline}完成。")
        elif name == '制造费用':
            tasks.append(f"设备部会同财务部核对能耗、维修、折旧和费用分摊，提交《制造费用分摊差异表》，{deadline}完成。")
    if not tasks:
        tasks = [f"财务部复核成本明细与预算差异，提交《月度成本差异核查表》，{deadline}完成。",
                 f"生产部复核单耗、工时和产量口径，提交《成本数据勾稽记录》，{deadline}完成。"]
    return "\n\n".join(f"{index}. {task}" for index, task in enumerate(tasks[:3], 1))


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
    available_products = [item['name'] for item in cost_service.get_products()]
    months = cost_service.get_months()
    selected_products = [p for p in (products or available_products) if p in available_products]
    if not selected_products:
        selected_products = list(available_products)

    elements = {
        'unit_cost': '单位成本',
        'material_cost': '直接材料',
        'labor_cost': '直接人工',
        'overhead_cost': '制造费用',
    }
    values = {key: [] for key in elements}
    for product_idx, product in enumerate(selected_products):
        for month_idx, month in enumerate(months):
            summary = cost_service.get_cost_summary(product, month) or {}
            for key in elements:
                value = summary.get(key)
                values[key].append([month_idx, product_idx, round(float(value), 2)] if value is not None else [month_idx, product_idx, None])

    return {
        'products': selected_products,
        'months': months,
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
    focus_required = bool(_focus_alerts(data))
    fallback = _build_attribution_fallback(data, waterfall, material)
    cached = None if force else _ATTRIBUTION_CACHE.get(cache_key)
    if cached:
        return {
            'product': product,
            'month': month,
            'data': context,
            'alerts': data.get('alerts', []),
            '重点分析': focus_required,
            'analysis': _ensure_focus_analysis(
                cached['analysis'] if cached.get('rag_sources') else _append_data_only_notice(cached['analysis']),
                data, waterfall, material,
            ),
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
                '重点分析': focus_required,
                'analysis': _ensure_focus_analysis(
                    cached['analysis'] if cached.get('rag_sources') else _append_data_only_notice(cached['analysis']),
                    data, waterfall, material,
                ),
                'analysis_source': cached.get('source', 'ai'),
                'rag_sources': cached.get('rag_sources', []),
                'rag_used': bool(cached.get('rag_sources')),
                'knowledge_base_version': 'knowledge_index_meta',
                'cached': True,
            }
        rag_results = _dashboard_rag_results(product, month, data, material) if focus_required else []
        rag_sources = _rag_source_names(rag_results)
        rag_context = '\n'.join(
            f"[{index + 1}] [来源：{result.get('source', '知识库')}] {result.get('content', '')[:500]}"
            for index, result in enumerate(rag_results)
        )
        source = 'fallback'
        try:
            from llm.prompts import DASHBOARD_ATTRIBUTION_PROMPT, SYSTEM_ROLE
            text = _model_text_with_timeout(
                DASHBOARD_ATTRIBUTION_PROMPT.format(
                    product=product,
                    month=month,
                    context=json.dumps(context, ensure_ascii=False, indent=2),
                    rag_context=rag_context or '知识库检索正在初始化或本次未检索到匹配片段；不得因此停止归因，请先基于看板数据生成分析，待核查事项明确标注。',
                ),
                SYSTEM_ROLE,
                _LLM_STREAM_TIMEOUT_SECONDS,
                max_tokens=_ATTRIBUTION_MAX_TOKENS,
            )
            if text and str(text).strip():
                source = 'ai'
        except Exception:
            text = fallback
        text = text or fallback
        used_rag_sources = rag_sources if source == 'ai' else []
        if used_rag_sources:
            text = _append_rag_sources(text, used_rag_sources, rag_results)
        else:
            text = _append_data_only_notice(text)
        text = _ensure_focus_analysis(text, data, waterfall, material)
        # 仅缓存模型生成结果；临时网络/超时兜底文本不应污染后续切换结果。
        if source == 'ai':
            _ATTRIBUTION_CACHE[cache_key] = {'analysis': text, 'source': source, 'rag_sources': used_rag_sources}
    return {
        'product': product,
        'month': month,
        'data': context,
        'alerts': data.get('alerts', []),
        '重点分析': focus_required,
        'analysis': text,
        'analysis_source': source,
        'rag_sources': used_rag_sources,
        'rag_used': bool(used_rag_sources),
        'knowledge_base_version': 'knowledge_index_meta',
        'cached': False,
    }


def dashboard_attribution_stream(product: str, month: str, force: bool = False):
    """以 SSE 事件片段生成看板归因，任何依赖失败都保证返回规则兜底。"""
    cache_key = (product, month)
    context = None
    fallback = "## 归因分析\n\n当前成本数据暂时不可用，请稍后重试。"
    try:
        data = three_dim_compare(product, month)
        if data.get('error'):
            yield {'event': 'complete', 'data': data}
            return
        waterfall = cost_waterfall(product, month)
        material = material_detail_table(product, month)
        context = {'dashboard': data, 'waterfall': waterfall, 'material_detail': material}
        focus_required = bool(_focus_alerts(data))
        fallback = _build_attribution_fallback(data, waterfall, material)
    except Exception:
        logger.exception("归因基础数据准备失败: product=%s month=%s", product, month)
        yield {'event': 'meta', 'data': {'product': product, 'month': month, 'alerts': [], '重点分析': False, 'rag_sources': []}}
        yield {'event': 'chunk', 'data': {'text': fallback}}
        yield {'event': 'complete', 'data': {
            'product': product, 'month': month, 'analysis': fallback,
            'analysis_source': 'fallback', 'alerts': [], '重点分析': False,
            'rag_sources': [], 'rag_used': False, 'data': {}, 'cached': False,
        }}
        return

    def result_payload(text, source, rag_sources, cached):
        return {
            'product': product, 'month': month, 'data': context,
            'alerts': data.get('alerts', []), '重点分析': focus_required,
            'analysis': text, 'analysis_source': source,
            'rag_sources': rag_sources, 'rag_used': bool(rag_sources),
            'knowledge_base_version': 'knowledge_index_meta', 'cached': cached,
        }

    def replay(text):
        for start in range(0, len(text), 32):
            yield {'event': 'chunk', 'data': {'text': text[start:start + 32]}}

    # 基础数据准备完就通知前端，避免在RAG/模型初始化期间一直显示加载。
    yield {'event': 'meta', 'data': {
        'product': product, 'month': month, 'alerts': data.get('alerts', []),
        '重点分析': focus_required, 'rag_sources': [],
    }}

    cached = None if force else _ATTRIBUTION_CACHE.get(cache_key)
    if cached:
        text = _ensure_focus_analysis(
            cached['analysis'] if cached.get('rag_sources') else _append_data_only_notice(cached['analysis']),
            data, waterfall, material,
        )
        yield from replay(text)
        yield {'event': 'complete', 'data': result_payload(text, cached.get('source', 'ai'), cached.get('rag_sources', []), True)}
        return

    with _attribution_lock(cache_key):
        cached = None if force else _ATTRIBUTION_CACHE.get(cache_key)
        if cached:
            text = _ensure_focus_analysis(
                cached['analysis'] if cached.get('rag_sources') else _append_data_only_notice(cached['analysis']),
                data, waterfall, material,
            )
            yield from replay(text)
            yield {'event': 'complete', 'data': result_payload(text, cached.get('source', 'ai'), cached.get('rag_sources', []), True)}
            return

        # RAG不是归因生成的必要条件，超过短时限即按无知识库处理。
        rag_results = _run_with_timeout(
            lambda: _dashboard_rag_results(product, month, data, material),
            _RAG_TIMEOUT_SECONDS,
            default=[],
        ) if focus_required else []
        rag_sources = _rag_source_names(rag_results)
        rag_context = '\n'.join(
            f"[{index + 1}] [来源：{result.get('source', '知识库')}] {result.get('content', '')[:500]}"
            for index, result in enumerate(rag_results)
        )
        source = 'fallback'
        text = ''
        streamed_text = False
        model_failed = False
        try:
            from llm.prompts import DASHBOARD_ATTRIBUTION_PROMPT, SYSTEM_ROLE
            prompt = DASHBOARD_ATTRIBUTION_PROMPT.format(
                product=product, month=month,
                context=json.dumps(context, ensure_ascii=False, indent=2),
                rag_context=rag_context or '知识库检索正在初始化或本次未检索到匹配片段；不得因此停止归因，请先基于看板数据生成分析，待核查事项明确标注。',
            )
            # Consume the provider's real streaming response. Each model
            # delta is forwarded immediately; the timeout guard prevents a
            # stalled upstream iterator from keeping the SSE request open.
            for chunk in _model_stream_with_timeout(
                prompt, SYSTEM_ROLE, _LLM_STREAM_TIMEOUT_SECONDS, max_tokens=_ATTRIBUTION_MAX_TOKENS
            ):
                if chunk:
                    text += str(chunk)
                    streamed_text = True
                    yield {'event': 'chunk', 'data': {'text': str(chunk)}}
            if not text.strip():
                raise RuntimeError('模型未返回归因文本')
            source = 'ai'
        except Exception as exc:
            # A provider can time out after it has already streamed useful
            # prose. Keep that model text visible instead of replacing it
            # with a generic fallback document.
            if streamed_text and text.strip():
                logger.warning("归因模型流式未完成，保留已生成内容: product=%s month=%s error=%s", product, month, exc)
                source = 'ai_partial'
            else:
                logger.warning("归因模型调用失败，使用规则兜底: product=%s month=%s error=%s", product, month, exc)
                model_failed = True
                text = fallback
                source = 'fallback'
        text = text or fallback
        used_rag_sources = rag_sources if source == 'ai' else []
        text = _append_rag_sources(text, used_rag_sources, rag_results) if used_rag_sources else _append_data_only_notice(text)
        text = _ensure_focus_analysis(text, data, waterfall, material)
        if source == 'ai':
            _ATTRIBUTION_CACHE[cache_key] = {'analysis': text, 'source': source, 'rag_sources': used_rag_sources}
        # 模型在首个chunk前失败时也补发完整文本，前端不会只看到空白。
        if source == 'fallback' and (model_failed or not streamed_text):
            # If some model deltas were already displayed, replace the partial
            # text instead of appending a duplicate fallback document.
            yield {'event': 'replace', 'data': {'text': text}}
        elif source == 'fallback':
            yield from replay(text)
        yield {'event': 'complete', 'data': result_payload(text, source, used_rag_sources, False)}


def _run_with_timeout(fn, timeout: float, default):
    """在守护线程执行可能阻塞的依赖调用，超时后立即返回默认值。"""
    result_queue = queue.Queue(maxsize=1)

    def worker():
        try:
            result_queue.put((True, fn()), block=False)
        except Exception as exc:
            result_queue.put((False, exc), block=False)

    threading.Thread(target=worker, daemon=True, name="dashboard-attribution-dependency").start()
    try:
        ok, value = result_queue.get(timeout=timeout)
        return value if ok else default
    except queue.Empty:
        logger.warning("归因依赖调用超时（%.1fs）", timeout)
        return default


def _model_stream_with_timeout(prompt: str, system: str, timeout: float, max_tokens: int = 1800):
    """消费LLM流式响应并设置总超时，避免SSE连接无限等待。"""
    # Do not use a bounded queue here. A fast provider can emit more than 128
    # deltas before the ASGI response gets scheduled; dropping the terminal
    # ``done`` marker would make the consumer wait until the hard timeout.
    result_queue = queue.Queue()

    def worker():
        try:
            from llm.client import llm_client
            for chunk in llm_client.chat_stream(prompt, system=system, max_tokens=max_tokens):
                if chunk:
                    result_queue.put(('chunk', str(chunk)))
            result_queue.put(('done', None))
        except Exception as exc:
            result_queue.put(('error', exc))

    threading.Thread(target=worker, daemon=True, name="dashboard-attribution-llm").start()
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"模型流式调用超过{timeout:.0f}秒")
        try:
            kind, value = result_queue.get(timeout=min(0.5, remaining))
        except queue.Empty:
            continue
        if kind == 'chunk':
            yield value
        elif kind == 'done':
            return
        else:
            raise value


def _model_text_with_timeout(prompt: str, system: str, timeout: float, max_tokens: int = 1800) -> str:
    """Call the complete-text API with a hard wall-clock timeout."""
    result_queue = queue.Queue(maxsize=1)

    def worker():
        try:
            from llm.client import llm_client
            result_queue.put((True, str(llm_client.chat(prompt, system=system, max_tokens=max_tokens) or "")), block=False)
        except Exception as exc:
            try:
                result_queue.put((False, exc), block=False)
            except queue.Full:
                pass

    threading.Thread(target=worker, daemon=True, name="dashboard-attribution-llm-text").start()
    try:
        ok, value = result_queue.get(timeout=timeout)
    except queue.Empty as exc:
        raise TimeoutError(f"模型调用超过{timeout:.0f}秒") from exc
    if not ok:
        raise value
    return value


def _dashboard_rag_results(product: str, month: str, data: dict, material: dict) -> list[dict]:
    """仅针对阈值告警检索归因依据，避免普通分析增加延迟。"""
    try:
        from rag.retriever import bm25_search, hybrid_search
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
        # Keyword retrieval is available from the persisted cache even while
        # Chroma/embedding initialization is still running. Use it first so a
        # cold-start vector model cannot block attribution generation.
        keyword_results = bm25_search(query, top_k=3)
        if keyword_results:
            return keyword_results
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


def _append_rag_sources(text: str, sources: list[str], results: list[dict] | None = None) -> str:
    """保留实际使用的知识库来源，并以简短摘要展示依据。"""
    marker = '## 知识库依据'
    existing_marker = re.search(r"(?m)^\s*#{1,6}\s*知识库(?:依据|来源)\s*[:：]?\s*$", str(text))
    evidence_lines = _compact_rag_evidence(sources, results)
    evidence_text = "\n".join(evidence_lines)
    if existing_marker:
        # The source chapter is always normalized from retrieved results so a
        # verbose model-generated citation cannot leak into the final report.
        prefix = str(text)[:existing_marker.start()].rstrip()
        heading = str(text)[existing_marker.start():existing_marker.end()].strip()
        return f"{prefix}\n\n{heading}\n\n{evidence_text}"
    return f"{str(text).rstrip()}\n\n{marker}\n\n{evidence_text}"


def _compact_source_body(value: str, max_chars: int = 110) -> str:
    """压缩模型生成的来源章节，保留文件名和核心依据。"""
    lines = []
    for raw_line in str(value or '').splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue
        if len(line) > max_chars:
            sentence = re.split(r"(?<=[。！？.!?；;])\s*", line)[0].strip()
            line = sentence if sentence and len(sentence) <= max_chars else f"{line[:max_chars - 1].rstrip()}…"
        if line not in lines:
            lines.append(line)
    return "\n".join(lines)


def _compact_rag_evidence(sources: list[str], results: list[dict] | None = None) -> list[str]:
    """每个知识库文件只展示一条简短、可追溯的依据。"""
    evidence_lines = []
    seen_sources = set()
    for result in results or []:
        source = str(result.get('source') or '知识库').strip()
        if not source or source in seen_sources:
            continue
        content = _compact_source_body(result.get('content', ''), max_chars=100)
        content = content.splitlines()[0] if content else '本次分析引用了该文档。'
        seen_sources.add(source)
        evidence_lines.append(f"- [来源：{source}] {content}")
    for source in sources:
        source = str(source or '').strip()
        if source and source not in seen_sources:
            seen_sources.add(source)
            evidence_lines.append(f"- [来源：{source}] 本次分析引用了该文档。")
    return evidence_lines or ["- 本次未使用知识库依据。"]


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
    if _focus_alerts(data):
        parts.append(_focus_analysis_section(data, waterfall, material))
    else:
        parts.append("## 常规分析")
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
