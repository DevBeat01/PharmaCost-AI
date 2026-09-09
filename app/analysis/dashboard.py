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
from config import ALERT_THRESHOLD, MONTHS, PRODUCTS

logger = logging.getLogger("analysis.dashboard")

_RAG_TIMEOUT_SECONDS = 4.0
# 首次模型加载及网络首包可能较慢；前端 SSE 超时同为 90 秒，服务端不应提前中断。
_LLM_STREAM_TIMEOUT_SECONDS = 90.0
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
    """Build a data-backed section that is present whenever a cost alert fires."""
    alerts = _focus_alerts(data)
    if not alerts:
        return ''

    rows = {row.get('metric'): row for row in data.get('rows', [])}
    lines = ["## 重点分析"]
    for alert in alerts:
        metric = str(alert.get('metric') or '成本要素')
        row = rows.get(metric, {})
        change = float(alert.get('change') or 0)
        direction = '上涨' if change > 0 else '下降'
        current = row.get('current')
        previous = row.get('last_month')
        value_text = (
            f"本月{float(current):.2f}，上月{float(previous):.2f}"
            if current is not None and previous is not None else "本月与上月数据需进一步核对"
        )
        verification = "请核查对应明细、原始凭证及成本分摊口径。"
        if '直接材料' in metric:
            top_material = (material.get('materials') or [None])[0]
            if top_material:
                verification = (
                    f"重点核查{top_material.get('material_name', '主要原材料')}的采购单价、领料单价和单位消耗；"
                    "其余材料及工艺单耗也需进一步核查。"
                )
            else:
                verification = "重点核查采购单价、领料单价、投料量和单位消耗。"
        elif '直接人工' in metric:
            verification = "重点核查工时、工资率、人员配置和产量摊薄情况。"
        elif '制造费用' in metric:
            verification = "重点核查能耗、维修、设备运行记录及费用分摊表。"
        elif '总成本' in metric:
            verification = "重点核查直接材料、直接人工、制造费用明细以及产量变化对总额的影响。"
        else:
            verification = "重点核查直接材料、直接人工、制造费用明细及其成本分摊口径。"
        lines.append(
            f"- {metric}{value_text}，环比{direction}{abs(change):.2f}%，已超过±{ALERT_THRESHOLD:.0f}%阈值。{verification}"
        )
    return '\n\n'.join(lines)


def _ensure_focus_analysis(text: str, data: dict, waterfall: dict, material: dict) -> str:
    """Guarantee a correct focus section even when the LLM omits or misformats it."""
    focus_section = _focus_analysis_section(data, waterfall, material)
    if not focus_section:
        return text

    text = str(text or '').strip()
    focus_heading = re.compile(r"(?m)^#{1,3}\s*重点分析\s*$")
    suggestion_heading = re.compile(r"(?m)^#{1,3}\s*改进建议\s*$")
    match = focus_heading.search(text)
    if match:
        next_heading = re.search(r"(?m)^#{1,3}\s+\S", text[match.end():])
        end = match.end() + next_heading.start() if next_heading else len(text)
        return f"{text[:match.start()].rstrip()}\n\n{focus_section}\n\n{text[end:].lstrip()}".strip()

    match = suggestion_heading.search(text)
    if match:
        return f"{text[:match.start()].rstrip()}\n\n{focus_section}\n\n{text[match.start():].lstrip()}".strip()
    return f"{text}\n\n{focus_section}".strip()


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
                rag_context=rag_context or '本次无阈值告警，不使用知识库。',
            )
            for chunk in _model_stream_with_timeout(
                prompt, SYSTEM_ROLE, _LLM_STREAM_TIMEOUT_SECONDS, max_tokens=1800
            ):
                if chunk:
                    text += str(chunk)
                    streamed_text = True
                    yield {'event': 'chunk', 'data': {'text': str(chunk)}}
            if not text.strip():
                raise RuntimeError('模型未返回归因文本')
            source = 'ai'
        except Exception as exc:
            logger.warning("归因模型调用失败，使用规则兜底: product=%s month=%s error=%s", product, month, exc)
            model_failed = True
            text = fallback
            source = 'fallback'
        text = text or fallback
        used_rag_sources = rag_sources if source == 'ai' else []
        text = _append_rag_sources(text, used_rag_sources) if used_rag_sources else _append_data_only_notice(text)
        text = _ensure_focus_analysis(text, data, waterfall, material)
        if source == 'ai':
            _ATTRIBUTION_CACHE[cache_key] = {'analysis': text, 'source': source, 'rag_sources': used_rag_sources}
        # 模型在首个chunk前失败时也补发完整文本，前端不会只看到空白。
        if source == 'fallback' and (model_failed or not streamed_text):
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
    result_queue = queue.Queue(maxsize=128)

    def worker():
        try:
            from llm.client import llm_client
            for chunk in llm_client.chat_stream(prompt, system=system, max_tokens=max_tokens):
                if chunk:
                    try:
                        result_queue.put(('chunk', str(chunk)), timeout=0.2)
                    except queue.Full:
                        return
            result_queue.put(('done', None), timeout=0.2)
        except Exception as exc:
            try:
                result_queue.put(('error', exc), timeout=0.2)
            except queue.Full:
                pass

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
    parts.append("## 重点分析" if _focus_alerts(data) else "## 常规分析")
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
