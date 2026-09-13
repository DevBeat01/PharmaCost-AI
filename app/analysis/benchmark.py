"""对标分析三步法引擎"""
import sys
import logging
import re
import json
import threading
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.cost_data import cost_service
from rag.citations import source_names

logger = logging.getLogger("benchmark")

_ATTRIBUTION_CACHE_PATH = Path(__file__).resolve().parent.parent / "output" / "benchmark_attribution_cache.json"
_ATTRIBUTION_CACHE_LOCK = threading.Lock()
# 归因格式规范化逻辑变更后，旧缓存需要重新生成。
_ATTRIBUTION_CACHE_VERSION = 6


def _attribution_cache_key(product: str, month: str) -> str:
    return f"{product}::{month}"


def _read_attribution_cache() -> dict:
    """读取持久化归因缓存；缓存损坏时降级为空，不影响分析。"""
    try:
        payload = json.loads(_ATTRIBUTION_CACHE_PATH.read_text(encoding="utf-8"))
        if payload.get("version") != _ATTRIBUTION_CACHE_VERSION:
            return {}
        return payload.get("entries", {}) if isinstance(payload.get("entries"), dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError, AttributeError):
        return {}


def _write_attribution_cache(entries: dict) -> None:
    """原子写入缓存，避免进程中断时留下半个 JSON 文件。"""
    _ATTRIBUTION_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = _ATTRIBUTION_CACHE_PATH.with_suffix(".tmp")
    payload = {"version": _ATTRIBUTION_CACHE_VERSION, "entries": entries}
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(_ATTRIBUTION_CACHE_PATH)


def _get_cached_attribution(product: str, month: str) -> dict | None:
    with _ATTRIBUTION_CACHE_LOCK:
        value = _read_attribution_cache().get(_attribution_cache_key(product, month))
    if not isinstance(value, dict) or not str(value.get("attribution", "")).strip():
        return None
    return value


def _cache_attribution(product: str, month: str, attribution: str, suggestions: list[dict], rag_sources: list[str], source: str) -> None:
    with _ATTRIBUTION_CACHE_LOCK:
        entries = _read_attribution_cache()
        entries[_attribution_cache_key(product, month)] = {
            "attribution": attribution,
            "suggestions": suggestions,
            "rag_sources": rag_sources,
            "analysis_source": source,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        _write_attribution_cache(entries)


def _build_attribution_fallback(diff_data: dict) -> str:
    """模型不可用时仍输出基于差异数据的可读归因，不虚构业务事实。"""
    rows = [row for row in diff_data.get("rows", []) if row.get("dimension") in {"直接材料", "直接人工", "制造费用", "单位成本"}]
    unit = next((row for row in rows if row["dimension"] == "单位成本"), {})
    factor_rows = [row for row in rows if row["dimension"] != "单位成本"]
    factor_rows.sort(key=lambda row: abs(float(row.get("diff_amount", 0))), reverse=True)
    direction = unit.get("direction", "")
    amount = abs(float(unit.get("diff_amount", 0)))
    rate = float(unit.get("diff_rate", 0))
    lines = [
        "1. 结论摘要",
        f"单位成本{direction}{amount:.2f}元/盒（差异率{rate:+.2f}%）。",
        "2. 重点分析",
    ]
    for index, row in enumerate(factor_rows, 1):
        lines.append(
            f"2.{index} {row['dimension']}：一厂{row['factory1']:.2f}元/盒，二厂{row['factory2']:.2f}元/盒，"
            f"差异{row['diff_amount']:+.2f}元/盒（{row['diff_rate']:+.2f}%）。具体业务原因需结合采购、工艺和设备记录核查。"
        )
    lines.extend(["3. 改进建议", "3.1 按差异贡献度优先核查成本要素的采购价格、单耗、人工效率和费用分摊依据。"])
    return "\n\n".join(lines) + "\n\n注：本结论仅基于成本数据。"


def _ensure_data_only_notice(text: str, rag_sources: list[str] | None) -> str:
    if rag_sources:
        return text
    marker = "本结论仅基于成本数据"
    return text if marker in text else f"{text.rstrip()}\n\n注：{marker}。"


def _append_rag_evidence(text: str, rag_results: list[dict] | None) -> str:
    """Attach retrieved evidence snippets to the attribution with file citations."""
    results = rag_results or []
    if not results:
        return str(text)
    existing_source = re.search(r"(?m)^\s*(?:#{1,6}\s*)?知识库(?:依据|来源)\s*[:：]?\s*$", str(text))
    if existing_source and re.search(r"\[来源：[^\]]+\]", str(text)):
        return str(text)
    lines = []
    seen = set()
    for result in results:
        source = str(result.get("source") or "知识库").strip()
        content = re.sub(r"\s+", " ", str(result.get("content") or "")).strip()
        if not content or (source, content) in seen:
            continue
        seen.add((source, content))
        lines.append(f"- [来源：{source}] {content[:300]}")
    if not lines:
        return str(text)
    evidence_text = "\n".join(lines)
    if existing_source:
        return f"{str(text).rstrip()}\n{evidence_text}"
    return f"{str(text).rstrip()}\n\n知识库依据：\n{evidence_text}"


def _default_suggestions(product: str, month: str, diff_data: dict) -> list[dict]:
    """根据最大差异要素生成可直接派发的兜底建议。"""
    factor_map = {
        "直接材料": ("采购部", "复核主要原材料供应商报价并引入至少两家供应商竞价。"),
        "直接人工": ("生产部", "复核单位工时、排班和加班数据，制定人工效率改善措施。"),
        "制造费用": ("设备部", "核查设备利用率及固定费用分摊，制定设备和产能优化措施。"),
    }
    rows = [r for r in diff_data.get("rows", []) if r.get("dimension") in factor_map]
    rows.sort(key=lambda r: abs(float(r.get("diff_amount", 0))), reverse=True)
    significant = [
        row for row in rows
        if abs(float(row.get("diff_rate", 0))) >= 3 or abs(float(row.get("diff_amount", 0))) >= 0.05
    ]
    rows = (significant or rows[:1])[:3]
    deadline = (datetime.strptime(month + "-28", "%Y-%m-%d") + timedelta(days=30)).strftime("%Y-%m-%d")
    suggestions = []
    for index, row in enumerate(rows, 1):
        department, action = factor_map[row["dimension"]]
        amount = float(row.get("diff_amount", 0))
        suggestions.append({
            "id": f"BENCH-{month}-{index:02d}",
            "suggestion": f"{action} 当前{row['dimension']}差异为{amount:+.2f}元/盒。",
            "department": department,
            "priority": "high" if abs(float(row.get("diff_rate", 0))) >= 10 else "medium",
            "expected_effect": f"缩小{row['dimension']}差异，目标改善≥{abs(amount):.2f}元/盒。",
            "deadline": deadline,
            "source_dimension": row["dimension"],
        })
    return suggestions


def _extract_suggestions(attribution: str, product: str, month: str, diff_data: dict) -> list[dict]:
    """仅提取数字编号的“改进建议”章节，避免正文被误生成为任务。"""
    text = str(attribution or "")
    match = re.search(r"^\s*3\.\s*改进建议\s*$([\s\S]*?)(?=^\s*\d+\.\s+[^\n]+\s*$|\Z)", text, re.MULTILINE)
    section = match.group(1) if match else ""
    items = []
    for line in section.splitlines():
        bullet = re.match(r"^\s*(?:3[.、]\d+|[-*•]|\d+[.)、])\s+(.+)$", line)
        if not bullet or "|" in line:
            continue
        value = re.sub(r"(?:\*\*|__|`)", "", bullet.group(1)).strip()
        if len(value) < 8 or value in {"建议", "改进建议"}:
            continue
        department = "采购部" if any(k in value for k in ("采购", "原材料", "供应商")) else (
            "生产部" if any(k in value for k in ("人工", "工时", "效率", "工艺")) else "设备部"
        )
        items.append({
            "id": f"BENCH-{month}-{len(items) + 1:02d}",
            "suggestion": value,
            "department": department,
            "priority": "medium",
            "expected_effect": "降低对标成本差异并改善相关指标。",
            "deadline": (datetime.strptime(month + "-28", "%Y-%m-%d") + timedelta(days=30)).strftime("%Y-%m-%d"),
            "source_dimension": "综合",
        })
        if len(items) == 3:
            break
    # 少于两项建议时，说明模型格式或内容不可靠，使用数据驱动的三要素建议。
    return items if len(items) >= 2 else _default_suggestions(product, month, diff_data)


def _normalize_model_attribution(text: str) -> str | None:
    """将模型的近似三段式输出归一化为固定格式，异常内容返回 None。

    不同模型经常把二级标题写成三级标题、中文序号标题，或在标题后补充说明。
    这些差异不影响内容本身，应在后端统一修正，而不是直接丢弃为兜底文本。
    """
    value = str(text or "").replace("\ufeff", "").replace("\r", "").strip()
    if not value or len(value) > 5000 or "```" in value or "<table" in value.lower():
        return None

    # 引用块会在前端造成不稳定展示，无法可靠判断层级时走兜底。
    if re.search(r"^\s*>+", value, re.MULTILINE):
        return None

    aliases = {
        "结论摘要": "结论摘要",
        "总体结论": "结论摘要",
        "结论": "结论摘要",
        "重点分析": "重点分析",
        "常规分析": "重点分析",
        "差异分析": "重点分析",
        "改进建议": "改进建议",
        "建议": "改进建议",
    }
    # 支持 ##、###、中英文数字序号和标题后的括号说明/冒号说明。
    heading_re = re.compile(
        r"^\s*(?:#{1,6}\s*)?(?:[一二三四五六七八九十百0-9]+[、.)．.]\s*)?"
        r"(?:\*\*|__)?\s*(结论摘要|总体结论|结论|重点分析|常规分析|差异分析|改进建议|建议)"
        r"\s*(?:\*\*|__)?(?:\s*[:：\-—（(].*)?\s*$"
    )
    sections = []
    current = None
    preamble = []
    for raw_line in value.splitlines():
        line = raw_line.strip()
        match = heading_re.match(line)
        if match:
            canonical = aliases[match.group(1)]
            if any(section[0] == canonical for section in sections):
                return None
            current = [canonical, []]
            sections.append(current)
            continue
        # 允许模型在首个标题前输出一句礼貌性前缀，但不把它带入报告。
        if current is None:
            if line:
                preamble.append(line)
            continue
        # 其他 Markdown 标题说明格式漂移，避免混入不稳定层级。
        if re.match(r"^\s*#{1,6}\s+", line):
            return None
        current[1].append(raw_line.rstrip())

    required = ["结论摘要", "重点分析", "改进建议"]
    if [section[0] for section in sections] != required:
        return None

    normalized = []
    for section_index, (heading, body_lines) in enumerate(sections, 1):
        # 去掉模型残留的强调标记，保留正文语义；前端无需再显示 Markdown 源码。
        cleaned_lines = []
        for raw_line in body_lines:
            line = raw_line.strip()
            # 将模型偶尔输出的 Markdown 表格转成普通要点，避免触发前端表格渲染。
            if "|" in line and len([cell for cell in line.split("|") if cell.strip()]) >= 2:
                if re.match(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$", line):
                    continue
                cells = line.strip("|").split("|")
                cells = [cell.strip() for cell in cells if cell.strip()]
                if len(cells) >= 2:
                    cleaned_lines.append(f"- {cells[0]}：{'；'.join(cells[1:])}")
                    continue
            cleaned_lines.append(raw_line.rstrip())
        body = "\n".join(cleaned_lines)
        body = re.sub(r"\*\*(.*?)\*\*", r"\1", body)
        body = re.sub(r"__(.*?)__", r"\1", body)
        body = re.sub(r"`([^`]+)`", r"\1", body)
        body = body.strip()
        if not body:
            return None
        normalized.append(f"{section_index}. {heading}")
        if section_index == 1:
            # 摘要只保留一个段落，前端按纯文本展示而非 Markdown。
            normalized.append(re.sub(r"\s+", " ", re.sub(r"^\s*(?:[-*•]|\d+[.)、])\s*", "", body)).strip())
            continue

        items, current = [], ""
        for raw_line in body.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            bullet = re.match(r"^\s*(?:[-*•]|\d+(?:[.、]\d+)?[.)、]?)\s+(.+)$", line)
            if bullet:
                if current:
                    items.append(current)
                current = bullet.group(1).strip()
            elif current:
                current = f"{current} {line}"
            else:
                current = line
        if current:
            items.append(current)
        if not items:
            return None
        for item_index, item in enumerate(items, 1):
            item = re.sub(r"\s+", " ", item).strip()
            normalized.append(f"{section_index}.{item_index} {item}")
    return "\n\n".join(normalized)


def _is_valid_model_attribution(text: str) -> bool:
    """兼容标题变体后，判断归因是否仍满足三段式结构。"""
    return _normalize_model_attribution(text) is not None


def benchmark_structure_tree(product: str, month: str) -> dict:
    """第二步结构树：成本要素节点下挂本厂原材料明细。"""
    breakdown = benchmark_breakdown(product, month)
    if "error" in breakdown:
        return breakdown

    material_details = cost_service.get_material_detail(product, month)
    material_children = [
        {
            "name": item["material_name"],
            "unit_cost": item["unit_cost"],
            "total_cost": item["total_cost"],
            "ratio": item["ratio"],
            "factory": "中药一厂",
        }
        for item in material_details
    ]
    children = []
    for item in breakdown["breakdown"]:
        node = {
            "name": item["dimension"],
            "diff_amount": item["diff_amount"],
            "contribution": item["contribution"],
            "children": material_children if item["dimension"] == "直接材料" else [],
        }
        children.append(node)
    return {
        "product": product,
        "month": month,
        "total_diff": breakdown["total_diff"],
        "tree": {
            "name": "单位成本差异",
            "diff_amount": breakdown["total_diff"],
            "children": children,
        },
    }


def benchmark_diff(product: str, month: str) -> dict:
    """第一步: 找差异 — 两厂各要素对比"""
    f1 = cost_service.get_cost_summary(product, month, "中药一厂")
    f2 = cost_service.get_benchmark(product, month)

    if not f1 or not f2:
        return {"error": "缺少对标数据"}

    dimensions = [
        ("直接材料", 'material_cost'),
        ("直接人工", 'labor_cost'),
        ("制造费用", 'overhead_cost'),
        ("单位成本", 'unit_cost'),
        ("产量", 'production'),
        ("总成本", 'total_cost'),
    ]

    rows = []
    for label, key in dimensions:
        v1 = f1[key]
        v2 = f2[key]
        diff = round(v1 - v2, 2)
        rate = round(diff / v2 * 100, 2) if v2 else 0
        direction = "一厂高" if diff > 0 else ("一厂低" if diff < 0 else "持平")
        rows.append({
            'dimension': label,
            'factory1': v1,
            'factory2': v2,
            'diff_amount': diff,
            'diff_rate': rate,
            'direction': direction,
        })

    return {
        'product': product,
        'month': month,
        'factory1_name': '中药一厂',
        'factory2_name': '中药二厂',
        'rows': rows,
    }


def benchmark_breakdown(product: str, month: str) -> dict:
    """第二步: 拆结构 — 各要素差异对总差异的贡献度"""
    diff_data = benchmark_diff(product, month)
    if 'error' in diff_data:
        return diff_data

    rows = diff_data['rows']
    # 找到单位成本行的差异作为总差异
    total_diff = 0
    for r in rows:
        if r['dimension'] in ('直接材料', '直接人工', '制造费用'):
            total_diff += r['diff_amount']
    total_diff = round(total_diff, 2)

    breakdown = []
    for r in rows:
        if r['dimension'] in ('直接材料', '直接人工', '制造费用'):
            contribution = round(r['diff_amount'] / total_diff * 100, 1) if abs(total_diff) > 0.001 else 0
            breakdown.append({
                'dimension': r['dimension'],
                'diff_amount': r['diff_amount'],
                'contribution': contribution,
            })

    return {
        'product': product,
        'month': month,
        'total_diff': total_diff,
        'breakdown': breakdown,
    }


async def benchmark_attribution(product: str, month: str, force: bool = False) -> dict:
    """第三步: 拆原因 — RAG+LLM生成差异归因"""
    diff_data = benchmark_diff(product, month)
    if 'error' in diff_data:
        return diff_data

    if not force:
        cached = _get_cached_attribution(product, month)
        if cached:
            return {
                'product': product,
                'month': month,
                'diff_data': diff_data,
                'attribution': _ensure_data_only_notice(cached['attribution'], cached.get('rag_sources')),
                'suggestions': cached.get('suggestions') or _default_suggestions(product, month, diff_data),
                'rag_sources': cached.get('rag_sources', []),
                'rag_used': bool(cached.get('rag_sources')),
                'knowledge_base_version': 'knowledge_index_meta',
                'analysis_source': cached.get('analysis_source', 'cache'),
                'cached': True,
            }

    # 获取RAG检索结果
    try:
        from rag.retriever import hybrid_search
        query = f"{product} 生产工艺 设备 成本差异 对标分析"
        rag_results = hybrid_search(query, top_k=3)
    except Exception:
        logger.exception("RAG检索失败")
        rag_results = []

    # 调用LLM生成归因
    source = 'ai'
    try:
        from llm.client import llm_client
        from llm.prompts import BENCHMARK_ATTRIBUTION_PROMPT, SYSTEM_ROLE

        context = f"产品: {product}, 月份: {month}\n"
        context += "对标差异数据:\n"
        contribution_map = {
            item.get("dimension"): item.get("contribution")
            for item in benchmark_breakdown(product, month).get("breakdown", [])
        }
        for r in diff_data['rows']:
            contribution = contribution_map.get(r['dimension'])
            contribution_text = f", 贡献度{contribution}%" if contribution is not None else ""
            context += (
                f"- {r['dimension']}: 一厂{r['factory1']}, 二厂{r['factory2']}, "
                f"差异{r['diff_amount']}({r['direction']}), 差异率{r['diff_rate']}%{contribution_text}\n"
            )
        rag_context = ""
        if rag_results:
            rag_context = "\n".join(
                f"[{i + 1}] (来源: {rr.get('source', '知识库')}) {rr.get('content', '')[:300]}"
                for i, rr in enumerate(rag_results)
            )
        prompt = BENCHMARK_ATTRIBUTION_PROMPT.format(context=context, rag_context=rag_context)
        attribution = llm_client.chat(prompt, system=SYSTEM_ROLE, max_tokens=1800)
        if not str(attribution or '').strip():
            raise RuntimeError("LLM未返回归因文本")
        normalized_attribution = _normalize_model_attribution(attribution)
        if normalized_attribution is None:
            logger.warning("LLM归因格式不符合三段式规范，使用统一兜底模板")
            attribution = _build_attribution_fallback(diff_data)
            source = 'normalized_fallback'
        else:
            # 统一标题级别、别名和强调标记，确保前端每次展示同一种报告结构。
            attribution = normalized_attribution
    except Exception:
        logger.exception("LLM归因分析生成失败")
        attribution = _build_attribution_fallback(diff_data)
        source = 'fallback'

    suggestions = _extract_suggestions(attribution, product, month, diff_data)
    rag_sources = source_names(rag_results)
    attribution = _append_rag_evidence(attribution, rag_results) if rag_sources else _ensure_data_only_notice(attribution, rag_sources)
    _cache_attribution(product, month, attribution, suggestions, rag_sources, source)
    return {
        'product': product,
        'month': month,
        'diff_data': diff_data,
        'attribution': attribution,
        'suggestions': suggestions,
        # 仅返回本次实际检索到并传入归因提示词的知识库来源。
        'rag_sources': rag_sources,
        'rag_used': bool(rag_sources),
        'knowledge_base_version': 'knowledge_index_meta',
        'analysis_source': source,
        'cached': False,
    }


def benchmark_attribution_stream(product: str, month: str, force: bool = False):
    """以 SSE 事件片段生成对标归因，结果结构与 benchmark_attribution 一致。"""
    diff_data = benchmark_diff(product, month)
    if 'error' in diff_data:
        yield {'event': 'complete', 'data': diff_data}
        return

    def result_payload(attribution, suggestions, rag_sources, source, cached):
        return {
            'product': product, 'month': month, 'diff_data': diff_data,
            'attribution': attribution, 'suggestions': suggestions,
            'rag_sources': rag_sources, 'rag_used': bool(rag_sources),
            'knowledge_base_version': 'knowledge_index_meta',
            'analysis_source': source, 'cached': cached,
        }

    def replay(text):
        for start in range(0, len(text), 32):
            yield {'event': 'chunk', 'data': {'text': text[start:start + 32]}}

    if not force:
        cached = _get_cached_attribution(product, month)
        if cached:
            attribution = _ensure_data_only_notice(cached['attribution'], cached.get('rag_sources'))
            suggestions = cached.get('suggestions') or _default_suggestions(product, month, diff_data)
            yield {'event': 'meta', 'data': {'product': product, 'month': month, 'diff_data': diff_data, 'rag_sources': cached.get('rag_sources', [])}}
            yield from replay(attribution)
            yield {'event': 'complete', 'data': result_payload(attribution, suggestions, cached.get('rag_sources', []), cached.get('analysis_source', 'cache'), True)}
            return

    try:
        from rag.retriever import hybrid_search
        rag_results = hybrid_search(f"{product} 生产工艺 设备 成本差异 对标分析", top_k=3)
    except Exception:
        logger.exception("RAG检索失败")
        rag_results = []
    rag_sources = source_names(rag_results)
    context = f"产品: {product}, 月份: {month}\n对标差异数据:\n"
    contribution_map = {
        item.get("dimension"): item.get("contribution")
        for item in benchmark_breakdown(product, month).get("breakdown", [])
    }
    for row in diff_data['rows']:
        contribution = contribution_map.get(row['dimension'])
        contribution_text = f", 贡献度{contribution}%" if contribution is not None else ""
        context += (
            f"- {row['dimension']}: 一厂{row['factory1']}, 二厂{row['factory2']}, "
            f"差异{row['diff_amount']}({row['direction']}), 差异率{row['diff_rate']}%{contribution_text}\n"
        )
    rag_context = "\n".join(
        f"[{i + 1}] (来源: {item.get('source', '知识库')}) {item.get('content', '')[:300]}"
        for i, item in enumerate(rag_results)
    )
    yield {'event': 'meta', 'data': {'product': product, 'month': month, 'diff_data': diff_data, 'rag_sources': rag_sources}}
    attribution = ''
    source = 'ai'
    try:
        from llm.client import llm_client
        from llm.prompts import BENCHMARK_ATTRIBUTION_PROMPT, SYSTEM_ROLE
        prompt = BENCHMARK_ATTRIBUTION_PROMPT.format(context=context, rag_context=rag_context)
        for chunk in llm_client.chat_stream(prompt, system=SYSTEM_ROLE):
            if chunk:
                attribution += str(chunk)
                yield {'event': 'chunk', 'data': {'text': str(chunk)}}
        if not attribution.strip():
            raise RuntimeError('LLM未返回归因文本')
        normalized = _normalize_model_attribution(attribution)
        if normalized is None:
            attribution = _build_attribution_fallback(diff_data)
            source = 'normalized_fallback'
        else:
            attribution = normalized
    except Exception:
        logger.exception("LLM归因分析生成失败")
        attribution = _build_attribution_fallback(diff_data)
        source = 'fallback'
    suggestions = _extract_suggestions(attribution, product, month, diff_data)
    attribution = _append_rag_evidence(attribution, rag_results) if rag_sources else _ensure_data_only_notice(attribution, rag_sources)
    _cache_attribution(product, month, attribution, suggestions, rag_sources, source)
    yield {'event': 'complete', 'data': result_payload(attribution, suggestions, rag_sources, source, False)}
