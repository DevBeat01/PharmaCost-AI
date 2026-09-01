"""报告生成引擎 — 编排 数据→计算→RAG→LLM→模板→导出"""
import sys
import logging
import re
from pathlib import Path
from datetime import datetime


def normalize_markdown_text(text: str) -> str:
    value = str(text or '').replace('\r\n', '\n').replace('\r', '\n')
    value = re.sub(r'```(?:\w+)?\s*\n?', '', value)
    value = re.sub(r'```', '', value)
    value = re.sub(r'^\s{0,3}#{1,6}\s*', '', value, flags=re.MULTILINE)
    value = re.sub(r'^\s*[-*+]\s+', '• ', value, flags=re.MULTILINE)
    value = re.sub(r'^\s*\d+[.)]\s+', '', value, flags=re.MULTILINE)
    value = re.sub(
        r'^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$',
        '', value, flags=re.MULTILINE)
    # 过滤没有形成完整表格的孤立管道行（常见于模型只输出表头，
    # 例如 ``| 序号 | 建议事项 | ... |``）。完整表格至少保留相邻的
    # 两行数据，交由 Word/PDF/前端渲染器转换为真实表格。
    lines = value.split('\n')
    pipe_row = lambda line: (
        '|' in line and len([part for part in line.strip().strip('|').split('|')
                             if part.strip()]) >= 2)
    kept = []
    for index, line in enumerate(lines):
        if pipe_row(line):
            prev = next((lines[j] for j in range(index - 1, -1, -1)
                         if lines[j].strip()), '')
            nxt = next((lines[j] for j in range(index + 1, len(lines))
                        if lines[j].strip()), '')
            if not (pipe_row(prev) or pipe_row(nxt)):
                continue
        kept.append(line)
    value = '\n'.join(kept)
    value = re.sub(r'^\s*[-*_]{3,}\s*$', '', value, flags=re.MULTILINE)
    value = re.sub(r'\*\*(.*?)\*\*', r'\1', value, flags=re.DOTALL)
    value = re.sub(r'__(.*?)__', r'\1', value, flags=re.DOTALL)
    value = re.sub(r'(?<!\*)\*(?!\s)(.*?)(?<!\s)\*', r'\1', value)
    value = re.sub(r'(?<!_)_(?!\s)(.*?)(?<!\s)_', r'\1', value)
    value = re.sub(r'`([^`]+)`', r'\1', value)
    value = re.sub(r'\n{3,}', '\n\n', value)
    return value.strip()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import get_report_template_path, PRODUCT_SPECS, ALERT_THRESHOLD
from data.cost_data import cost_service
from rag.citations import citation_text, source_names

logger = logging.getLogger("report.engine")
from analysis.dashboard import (
    three_dim_compare, cost_trend, material_detail_table, overhead_detail_table,
    labor_metrics,
)
from analysis.benchmark import benchmark_diff, benchmark_breakdown
try:
    from llm.client import llm_client
    from llm.prompts import (
        SYSTEM_ROLE, MATERIAL_ATTRIBUTION_PROMPT, LABOR_ATTRIBUTION_PROMPT,
        OVERHEAD_ATTRIBUTION_PROMPT, BENCHMARK_ATTRIBUTION_PROMPT,
        IMPROVEMENT_PROMPT, TASK_GENERATION_PROMPT,
    )
    _HAS_LLM = True
except ImportError:
    _HAS_LLM = False

try:
    from rag.retriever import hybrid_search
    _HAS_RAG = True
except ImportError:
    _HAS_RAG = False
from report.template_parser import TemplateParser
from report.tables import (
    build_material_detail_table, build_overhead_detail_table,
    build_labor_table, build_trend_table, build_benchmark_diff_table,
    build_suggestion_table, build_task_table, _chg, _money2, _pct,
    _parse_tasks_json,
)


def _safe_llm_call(
    prompt: str, context: str, fallback: str = "", **format_values: str
) -> str:
    """安全调用LLM，失败时返回fallback"""
    if not _HAS_LLM:
        return fallback
    values = {
        "context": context,
        "rag_context": "",
        "market_info": "",
        "product": "",
        "month": "",
    }
    values.update(format_values)
    full_prompt = prompt.format(**values)
    try:
        return llm_client.chat(full_prompt, system=SYSTEM_ROLE)
    except Exception:
        logger.warning("LLM调用失败,使用fallback")
        return fallback


def _rag_search_safe(query: str, top_k: int = 3) -> list[dict]:
    """安全RAG检索"""
    if not _HAS_RAG:
        return []
    try:
        return hybrid_search(query, top_k=top_k)
    except Exception:
        return []


def _rag_to_str(results: list[dict]) -> str:
    """RAG结果格式化为上下文字符串"""
    if not results:
        return ""
    lines = []
    for i, r in enumerate(results):
        src = r.get('source', '知识库')
        lines.append(f"[{i+1}] (来源: {src}) {r['content'][:300]}")
    return "\n".join(lines)


class ReportEngine:
    """月度成本分析报告生成引擎"""

    def generate(self, product: str, month: str, output_path: str, report_type: str = "monthly"):
        """生成完整Word报告并返回可用于预览的元数据。"""
        # ===== Phase 1: 加载数据（仅首次） =====
        if not cost_service._loaded:
            cost_service.load_all()

        # ===== Phase 2: 计算指标 =====
        td = three_dim_compare(product, month)
        trend = cost_trend(product)
        mat_data = material_detail_table(product, month)
        oh_data = overhead_detail_table(product, month)
        lab = labor_metrics(product, month)
        bench = benchmark_diff(product, month)
        bench_bd = benchmark_breakdown(product, month)
        market = cost_service.get_market_prices(month)
        budget = cost_service.get_budget(product, month)

        # 当月和上月汇总
        cur = cost_service.get_cost_summary(product, month)
        prev_month = self._prev_month(month)
        prev = cost_service.get_cost_summary(product, prev_month) if prev_month else None
        last_year = cost_service.get_cost_summary_last_year(product, month)

        # 原材料上月对比
        mat_prev_map = cost_service.get_material_detail_prev(product, month)

        # 制造费用上月对比
        oh_prev_data = overhead_detail_table(product, prev_month) if prev_month else None
        oh_prev_map = {item['category']: item for item in oh_prev_data['overheads']} if oh_prev_data else {}

        # ===== Phase 3: RAG检索 =====
        rag_mat = _rag_search_safe(f"{product} 原材料 配方 金银花 黄芩 生产工艺")
        rag_oh = _rag_search_safe(f"{product} 设备 折旧 制造费用 GMP")
        rag_bench = _rag_search_safe(f"{product} 生产工艺 设备 成本差异 对标")
        rag_gmp = _rag_search_safe(f"{product} GMP 质量 生产规范")
        rag_recipe = _rag_search_safe(f"{product} 配方 原料")
        rag_industry = _rag_search_safe(f"行业基准 中成药 成本 {product}")

        # ===== Phase 4: LLM生成文本 =====
        mat_ctx = self._material_context(mat_data, market, mat_prev_map)
        material_rag = [*rag_mat, *rag_recipe]
        material_sources = source_names(material_rag)
        overhead_sources = source_names(rag_oh)
        benchmark_sources = source_names(rag_bench)
        gmp_sources = source_names(rag_gmp)
        industry_sources = source_names(rag_industry)
        # 改进建议综合了前序分析和 GMP 检索内容，引用需覆盖全部知识库输入。
        improvement_sources = list(dict.fromkeys(
            material_sources + overhead_sources + benchmark_sources + gmp_sources))

        mat_text = _safe_llm_call(
            MATERIAL_ATTRIBUTION_PROMPT, mat_ctx,
            "材料成本受中药材市场行情和采购策略综合影响。",
            rag_context=_rag_to_str(material_rag))

        lab_ctx = self._labor_context(lab, product, month)
        lab_text = _safe_llm_call(
            LABOR_ATTRIBUTION_PROMPT, lab_ctx, "人工成本受产量摊薄和效率变化影响。")

        oh_ctx = self._overhead_context(oh_data, oh_prev_map)
        oh_text = _safe_llm_call(
            OVERHEAD_ATTRIBUTION_PROMPT, oh_ctx,
            "制造费用受产能利用率和固定费用摊薄效应影响。",
            rag_context=_rag_to_str(rag_oh))

        bench_ctx = self._bench_context(bench)
        bench_text = _safe_llm_call(
            BENCHMARK_ATTRIBUTION_PROMPT, bench_ctx,
            "两厂差异主要源于采购策略和设备自动化程度。",
            rag_context=_rag_to_str(rag_bench))

        anomaly_text = _safe_llm_call(
            LABOR_ATTRIBUTION_PROMPT,  # 复用prompt分析趋势异常
            self._trend_context(trend, product),
            "从趋势数据看，成本整体保持平稳，个别月份受季节因素影响有小幅波动。")

        highlights, issues = self._generate_highlights_issues(td, bench, lab)

        improvement_text = self._generate_improvements(
            mat_text, oh_text, bench_text, bench_ctx, _rag_to_str(rag_gmp))
        task_text = self._generate_tasks(product, month, improvement_text)

        # 仅汇总实际传入模型提示词的知识库来源，避免将未使用的检索结果写入报告。
        recipe_ref = citation_text(material_sources)
        process_ref = citation_text(overhead_sources)
        gmp_ref = citation_text(gmp_sources)
        industry_ref = citation_text(industry_sources)

        # 波动告警
        alerts = td.get('alerts', [])
        alert_desc = "；".join(a['message'] for a in alerts) if alerts else "各项指标波动均在正常范围内（阈值±10%）"

        # ===== Phase 5: 构建占位符映射 =====
        mat_display_text = self._with_rag_citation(normalize_markdown_text(mat_text), material_sources)
        oh_display_text = self._with_rag_citation(normalize_markdown_text(oh_text), overhead_sources)
        bench_display_text = self._with_rag_citation(normalize_markdown_text(bench_text), benchmark_sources)
        anomaly_display_text = normalize_markdown_text(anomaly_text)
        improvement_display_text = self._with_rag_citation(normalize_markdown_text(improvement_text), improvement_sources)
        ph = self._build_placeholders(
            product, month, td, trend, mat_data, oh_data, lab, bench,
            bench_bd, cur, prev, last_year, budget, oh_prev_map,
            mat_display_text, oh_display_text, bench_display_text,
            anomaly_display_text, improvement_display_text, highlights, issues,
            alert_desc, recipe_ref, process_ref, gmp_ref, industry_ref, report_type,
        )

        # ===== Phase 6: 填充模板并导出 =====
        parser = TemplateParser(get_report_template_path())
        parser.find_placeholders()

        # 插入动态表格（在占位符段落之后插入全新表格）
        price_rows = self._build_price_tracking_table(product, month, mat_data, market)

        parser.find_and_insert_new_table(
            "原材料成本明细表格",
            ["序号", "原材料名称", "本月单价(元/盒)", "上月单价(元/盒)", "环比变动", "变动原因初步判断"],
            build_material_detail_table(product, month))
        parser.find_and_insert_new_table(
            "近6个月成本趋势表格",
            ["月份", "产量(盒)", "单位材料(元/盒)", "单位人工(元/盒)", "单位制造费用(元/盒)", "单位成本(元/盒)", "环比变动"],
            build_trend_table(product))
        parser.find_and_insert_new_table(
            "原材料价格跟踪表格",
            ["原材料", "年初价", "本月价", "涨幅", "市场趋势", "对材料成本影响"],
            price_rows)
        parser.find_and_insert_new_table(
            "对标差异表格",
            ["对比维度", "中药一厂", "中药二厂", "差异金额", "差异率", "方向"],
            build_benchmark_diff_table(product, month))
        suggestion_rows = build_suggestion_table(product, month, improvement_text)
        # 建议表中的措施同样来自带 GMP/工艺上下文的生成结果，逐行保留
        # 来源标注，避免正文有引用而表格内容失去出处。
        if improvement_sources:
            suggestion_citation = f"（{citation_text(improvement_sources)}）"
            for row in suggestion_rows:
                if len(row) > 1 and suggestion_citation not in row[1]:
                    row[1] = f"{row[1]} {suggestion_citation}"

        parser.find_and_insert_new_table(
            "改进建议表格",
            ["序号", "建议事项", "责任部门", "优先级", "预期效果", "建议完成时间"],
            suggestion_rows)
        parser.find_and_insert_new_table(
            "整改任务表格",
            ["任务编号", "任务标题", "责任人", "优先级", "来源", "截止时间"],
            build_task_table(product, month, task_text))

        # 替换所有占位符
        parser.replace_placeholders(ph)
        parser.replace_table_placeholders(ph)
        all_rag_sources = material_sources + overhead_sources + benchmark_sources + gmp_sources + industry_sources + improvement_sources
        parser.append_knowledge_sources(
            self._unique_references(all_rag_sources)
            if all_rag_sources else ["本报告结论仅基于成本数据（未使用知识库）"]
        )

        # 保存
        parser.save(output_path)
        print(f"报告生成完成: {output_path}")

        # 整改任务摘要（预览不展示原始 JSON）
        task_items = _parse_tasks_json(task_text) if task_text else []
        if task_items:
            task_titles = "、".join(
                str(item.get('task_title') or item.get('title') or f"任务{i+1}")
                for i, item in enumerate(task_items[:5])
            )
            task_summary = (
                f"已自动生成 {len(task_items)} 项整改任务：{task_titles}"
                + ("等。" if len(task_items) > 5 else "。")
            )
        else:
            task_summary = "整改任务已按默认方案生成，详见整改任务表格。"

        return {
            'title': ph.get('报告标题', ''),
            'product': product,
            'month': month,
            'report_type': report_type,
            'generated_at': ph.get('编制日期', ''),
            'sections': [
                {'title': '总成本概览', 'content': f"单位成本：{ph.get('本月单位成本', '—')}，环比：{ph.get('单位成本环比', '—')}%。总成本：{ph.get('本月总成本', '—')}。"},
                {'title': '成本要素明细分析', 'content': f"材料成本：{ph.get('本月材料成本', '—')}，环比：{ph.get('材料成本环比', '—')}%；人工成本：{ph.get('本月人工成本', '—')}，环比：{ph.get('人工成本环比', '—')}%；制造费用：{ph.get('本月制造费用', '—')}，环比：{ph.get('制造费用环比', '—')}%。"},
                {'title': '重点产品专项分析', 'content': mat_display_text},
                {'title': '对标与异常分析', 'content': f"{bench_display_text}\n{anomaly_display_text}"},
                {'title': '总结与建议', 'content': f"{improvement_display_text}\n\n{task_summary}"},
            ],
            'references': self._unique_references(
                all_rag_sources),
            'rag_used': bool(all_rag_sources),
            'rag_notice': '知识库来源已标注' if all_rag_sources else '本报告结论仅基于成本数据（未使用知识库）',
            'knowledge_base_version': 'knowledge_index_meta',
            'placeholders': ph,
        }

    # ==================== 辅助方法 ====================

    @staticmethod
    def _prev_month(month: str) -> str:
        """获取上月字符串 (2026-06 -> 2026-05)"""
        parts = month.split('-')
        y, m = int(parts[0]), int(parts[1])
        m -= 1
        if m == 0:
            y -= 1
            m = 12
        return f"{y}-{m:02d}"

    @staticmethod
    def _f(val, default="—"):
        """安全格式化数值，None时返回default"""
        return str(val) if val is not None else default

    @staticmethod
    def _safe_pct(val):
        """安全格式化百分比"""
        return f"{val:.2f}" if val is not None else "—"

    @staticmethod
    def _with_rag_citation(text: str, sources: list[str]) -> str:
        citation = citation_text(sources)
        return f"{text}\n\n{citation}" if citation else text

    @staticmethod
    def _unique_references(sources: list[str]) -> list[str]:
        return [f"《{source}》" for source in dict.fromkeys(sources)]

    # ==================== 上下文构建 ====================

    def _material_context(self, mat_data, market, prev_map):
        lines = [f"产品: {mat_data['product']}, 月份: {mat_data['month']}"]
        lines.append("原材料明细:")
        for m in mat_data['materials']:
            prev_cost = prev_map.get(m['material_name'], 0)
            pct = cost_service.calc_mom_change(m['unit_cost'], prev_cost) if prev_cost else None
            lines.append(f"- {m['material_name']}: 本月{m['unit_cost']:.4f}元/盒, "
                         f"上月{prev_cost:.4f}元/盒, 环比{self._safe_pct(pct)}%")
        if market:
            lines.append("\n市场价格行情:")
            for mp in market[:6]:
                lines.append(f"- {mp['material_name']}: {mp['current_price']}{mp['unit']}, 趋势: {mp['trend']}")
        return "\n".join(lines)

    def _labor_context(self, lab, product, month):
        if 'error' in lab:
            return f"产品: {product}, 月份: {month}, 暂无详细人工数据"
        lines = [f"产品: {product}, 月份: {month}"]
        for key, m in lab['metrics'].items():
            lines.append(f"- {key}: 本月{m['current']:.2f}, 上月{m['prev']:.2f}, "
                         f"环比{self._safe_pct(m['change'])}%")
        return "\n".join(lines)

    def _overhead_context(self, oh_data, prev_map):
        lines = [f"产品: {oh_data['product']}, 月份: {oh_data['month']}"]
        for item in oh_data['overheads']:
            prev_item = prev_map.get(item['category'], {})
            prev_val = prev_item.get('unit_cost', 0) if isinstance(prev_item, dict) else prev_item
            pct = cost_service.calc_mom_change(item['unit_cost'], prev_val) if prev_val else None
            lines.append(f"- {item['category']}: 本月{item['unit_cost']:.4f}元/盒, "
                         f"上月{prev_val:.4f}元/盒, 环比{self._safe_pct(pct)}%")
        return "\n".join(lines)

    def _bench_context(self, bench):
        if 'error' in bench:
            return "暂无对标数据"
        lines = [f"产品: {bench['product']}, 月份: {bench['month']}"]
        for r in bench['rows']:
            lines.append(f"- {r['dimension']}: 一厂{r['factory1']:.2f}, "
                         f"二厂{r['factory2']:.2f}, 差异{r['diff_amount']:.2f}({r['direction']})")
        return "\n".join(lines)

    def _trend_context(self, trend, product):
        lines = [f"产品: {product} 近6个月趋势:"]
        for i in range(len(trend['months'])):
            lines.append(f"- {trend['months'][i]}: 产量{trend['production'][i]:,}盒, "
                         f"单位成本{trend['unit_cost'][i]:.4f}元/盒")
        return "\n".join(lines)

    def _build_price_tracking_table(self, product, month, mat_data, market):
        """构建原材料价格跟踪表格数据"""
        rows = []
        month_num = int(month.split('-')[1])
        for m in mat_data.get('materials', []):
            mname = m['material_name']
            matched = cost_service.get_market_price_for_material(mname)
            if not matched:
                rows.append([mname, "—", "—", "—", "—", "—"])
                continue
            jan_price = matched['prices'].get(f"{month[:4]}-01", 0)
            cur_price = matched['current_price']
            if jan_price and jan_price > 0:
                pct = round((cur_price - jan_price) / jan_price * 100, 2)
                pct_str = f"{pct:+.2f}%"
            else:
                pct = 0
                pct_str = "—"
            if abs(pct) >= 10:
                impact = "影响显著，需关注采购策略"
            elif abs(pct) >= 5:
                impact = "有一定影响，建议关注"
            else:
                impact = "影响较小"
            rows.append([
                mname,
                f"{jan_price}" if jan_price else "—",
                f"{cur_price}" if cur_price else "—",
                pct_str,
                matched['trend'],
                impact,
            ])
        return rows

    # ==================== LLM文本生成 ====================

    def _generate_highlights_issues(self, td, bench, lab):
        highlights = []
        issues = []

        # 从三维对比数据判断
        for row in td.get('rows', []):
            mom = row.get('mom_change')
            if mom is not None:
                if abs(mom) >= ALERT_THRESHOLD:
                    if mom < 0:
                        highlights.append(f"{row['metric']} 环比下降{abs(mom):.2f}%，成本控制效果明显")
                    else:
                        issues.append(f"{row['metric']} 环比上升{mom:.2f}%，超过告警阈值{ALERT_THRESHOLD}%")
                elif abs(mom) < 3:
                    highlights.append(f"{row['metric']} 保持稳定，波动仅{abs(mom):.2f}%")

        # 从对标数据判断
        if 'rows' in bench:
            for r in bench['rows']:
                if r['direction'] == '一厂低' and r['dimension'] in ('直接材料', '单位成本'):
                    highlights.append(f"{r['dimension']}低于中药二厂{abs(r['diff_rate']):.2f}%，保持竞争优势")
                elif r['direction'] == '一厂高' and abs(r['diff_rate']) > 5:
                    issues.append(f"{r['dimension']}高于中药二厂{r['diff_rate']:.2f}%，需关注差距")

        if not highlights:
            highlights.append("本月各项成本指标总体保持稳定")
        if not issues:
            issues.append("暂无显著异常，持续关注市场行情变化")

        return "；".join(highlights[:4]), "；".join(issues[:4])

    def _generate_improvements(self, mat_text, oh_text, bench_text, bench_ctx, rag_context):
        ctx = f"材料成本分析:\n{mat_text}\n\n制造费用分析:\n{oh_text}\n\n对标分析:\n{bench_text}"
        return _safe_llm_call(
            IMPROVEMENT_PROMPT, ctx,
            '1. 优化原材料采购策略\n2. 提高设备利用率\n3. 加强对标学习',
            rag_context=rag_context)

    def _generate_tasks(self, product, month, improvement_text):
        return _safe_llm_call(
            TASK_GENERATION_PROMPT,
            improvement_text,
            "",
            product=product,
            month=month,
        )

    # ==================== 占位符映射构建 ====================

    def _build_placeholders(self, product, month, td, trend, mat_data, oh_data,
                            lab, bench, bench_bd, cur, prev, last_year,
                            budget, oh_prev_map, mat_text, oh_text,
                            bench_text, anomaly_text, improvement_text,
                            highlights, issues, alert_desc, recipe_ref,
                            process_ref, gmp_ref, industry_ref,
                            report_type="monthly"):
        """构建完整的占位符字典"""
        ph = {}
        report_labels = {
            "monthly": "月度成本分析",
            "quarterly": "季度成本分析",
            "topic": "专题分析",
        }
        report_label = report_labels.get(report_type, report_labels["monthly"])

        # --- 基本信息 ---
        ym = month.replace('-', '年') + '月'
        ph['报告标题'] = f"{product}{ym}{report_label}报告"
        ph['报告编号'] = f"RPT-{month.replace('-', '')}-{product[:2]}"
        ph['分析月份'] = month
        # 报告生成时刻，精确到秒，避免封面仍显示旧的仅日期值。
        # 报告模板要求编制日期仅显示日期，不包含时分秒。
        ph['编制日期'] = datetime.now().strftime('%Y-%m-%d')
        ph['报告类型'] = f"{report_label}报告"
        ph['产品名称'] = product
        ph['产品规格'] = PRODUCT_SPECS.get(product, '')

        # --- 三维对比数据提取 ---
        row_map = {}
        for r in td.get('rows', []):
            row_map[r['metric']] = r

        prod_row = row_map.get('产量(盒)', {})
        uc_row = row_map.get('单位成本(元/盒)', {})
        tc_row = row_map.get('总成本(元)', {})
        mat_row = row_map.get('直接材料(元/盒)', {})
        lab_row = row_map.get('直接人工(元/盒)', {})
        oh_row = row_map.get('制造费用(元/盒)', {})

        # --- 2.1 核心指标 ---
        ph['本月产量'] = self._f(prod_row.get('current'))
        ph['上月产量'] = self._f(prod_row.get('last_month'))
        ph['产量环比'] = self._safe_pct(prod_row.get('mom_change'))
        ph['去年同月产量'] = self._f(prod_row.get('last_year'))
        ph['产量同比'] = self._safe_pct(prod_row.get('yoy_change'))
        ph['预算产量'] = self._f(prod_row.get('budget'))
        ph['产量预算偏差'] = self._safe_pct(prod_row.get('budget_deviation'))

        ph['本月单位成本'] = self._f(uc_row.get('current'))
        ph['上月单位成本'] = self._f(uc_row.get('last_month'))
        ph['单位成本环比'] = self._safe_pct(uc_row.get('mom_change'))
        ph['去年单位成本'] = self._f(uc_row.get('last_year'))
        ph['单位成本同比'] = self._safe_pct(uc_row.get('yoy_change'))
        ph['预算单位成本'] = self._f(uc_row.get('budget'))
        ph['单位成本预算偏差'] = self._safe_pct(uc_row.get('budget_deviation'))

        ph['本月总成本'] = self._f(tc_row.get('current'))
        ph['上月总成本'] = self._f(tc_row.get('last_month'))
        ph['总成本环比'] = self._safe_pct(tc_row.get('mom_change'))
        ph['去年总成本'] = self._f(tc_row.get('last_year'))
        ph['总成本同比'] = self._safe_pct(tc_row.get('yoy_change'))
        ph['预算总成本'] = self._f(tc_row.get('budget'))
        ph['总成本预算偏差'] = self._safe_pct(tc_row.get('budget_deviation'))

        ph['本月材料成本'] = self._f(mat_row.get('current'))
        ph['上月材料成本'] = self._f(mat_row.get('last_month'))
        ph['材料成本环比'] = self._safe_pct(mat_row.get('mom_change'))
        ph['预算材料成本'] = self._f(mat_row.get('budget'))
        ph['材料预算偏差'] = self._safe_pct(mat_row.get('budget_deviation'))

        ph['本月人工成本'] = self._f(lab_row.get('current'))
        ph['上月人工成本'] = self._f(lab_row.get('last_month'))
        ph['人工成本环比'] = self._safe_pct(lab_row.get('mom_change'))
        ph['预算人工成本'] = self._f(lab_row.get('budget'))
        ph['人工预算偏差'] = self._safe_pct(lab_row.get('budget_deviation'))

        ph['本月制造费用'] = self._f(oh_row.get('current'))
        ph['上月制造费用'] = self._f(oh_row.get('last_month'))
        ph['制造费用环比'] = self._safe_pct(oh_row.get('mom_change'))
        ph['预算制造费用'] = self._f(oh_row.get('budget'))
        ph['制造费用预算偏差'] = self._safe_pct(oh_row.get('budget_deviation'))

        # --- 2.2 成本结构 ---
        total_unit = cur['unit_cost'] if cur else 1
        m_amt = cur['material_cost'] if cur else 0
        l_amt = cur['labor_cost'] if cur else 0
        o_amt = cur['overhead_cost'] if cur else 0

        ph['材料金额'] = _money2(m_amt)
        ph['材料占比'] = _pct(round(m_amt / total_unit * 100, 1) if total_unit else 0)
        ph['材料环比'] = self._safe_pct(mat_row.get('mom_change'))

        ph['人工金额'] = _money2(l_amt)
        ph['人工占比'] = _pct(round(l_amt / total_unit * 100, 1) if total_unit else 0)
        ph['人工环比'] = self._safe_pct(lab_row.get('mom_change'))

        ph['制造费用金额'] = _money2(o_amt)
        ph['制造费用占比'] = _pct(round(o_amt / total_unit * 100, 1) if total_unit else 0)
        ph['制造费用环比'] = self._safe_pct(oh_row.get('mom_change'))

        ph['单位成本'] = _money2(total_unit)
        ph['总环比'] = self._safe_pct(uc_row.get('mom_change'))

        # 贡献度 = 要素变动额 / 总变动额 × 100
        if prev:
            m_diff = m_amt - prev['material_cost']
            l_diff = l_amt - prev['labor_cost']
            o_diff = o_amt - prev['overhead_cost']
            total_diff = m_diff + l_diff + o_diff
            if total_diff != 0:
                ph['材料贡献度'] = _pct(round(m_diff / total_diff * 100, 1))
                ph['人工贡献度'] = _pct(round(l_diff / total_diff * 100, 1))
                ph['制造费用贡献度'] = _pct(round(o_diff / total_diff * 100, 1))
            else:
                ph['材料贡献度'] = '—'
                ph['人工贡献度'] = '—'
                ph['制造费用贡献度'] = '—'
        else:
            ph['材料贡献度'] = '—'
            ph['人工贡献度'] = '—'
            ph['制造费用贡献度'] = '—'

        # --- 波动告警 ---
        ph['波动告警描述'] = alert_desc

        # --- 3.1 材料分析文本 ---
        ph['材料成本归因分析文本'] = mat_text

        # --- 3.2 人工指标 ---
        if 'metrics' in lab:
            m = lab['metrics']
            ph['人工单位成本'] = _money2(m['unit_labor_cost']['current'])
            ph['上月人工单位成本'] = _money2(m['unit_labor_cost']['prev'])
            ph['人工环比'] = self._safe_pct(m['unit_labor_cost']['change'])
            ph['本月工时'] = _money2(m['labor_hours_per_10k']['current'])
            ph['上月工时'] = _money2(m['labor_hours_per_10k']['prev'])
            ph['工时环比'] = self._safe_pct(m['labor_hours_per_10k']['change'])
            ph['本月时薪'] = _money2(m['avg_hourly_wage']['current'])
            ph['上月时薪'] = _money2(m['avg_hourly_wage']['prev'])
            ph['时薪环比'] = self._safe_pct(m['avg_hourly_wage']['change'])
            ph['本月效率'] = _money2(m['labor_efficiency']['current'])
            ph['上月效率'] = _money2(m['labor_efficiency']['prev'])
            ph['效率环比'] = self._safe_pct(m['labor_efficiency']['change'])
        else:
            for k in ['人工单位成本', '上月人工单位成本', '人工环比',
                       '本月工时', '上月工时', '工时环比',
                       '本月时薪', '上月时薪', '时薪环比',
                       '本月效率', '上月效率', '效率环比']:
                ph[k] = '—'

        # --- 3.3 制造费用明细 ---
        category_map = {
            '折旧费': ('本月折旧', '上月折旧', '折旧环比', '折旧变动说明'),
            '动力费': ('本月动力', '上月动力', '动力环比', '动力变动说明'),
            '间接人工': ('本月间接人工', '上月间接人工', '间接人工环比', '间接人工变动说明'),
            '检验费': ('本月检验', '上月检验', '检验环比', '检验变动说明'),
            '其他制造费用': ('本月其他', '上月其他', '其他环比', '其他变动说明'),
        }
        total_cur_oh = 0
        total_prev_oh = 0
        for item in oh_data.get('overheads', []):
            cat = item['category']
            total_cur_oh += item['unit_cost']
            prev_item = oh_prev_map.get(cat, {})
            prev_val = prev_item.get('unit_cost', 0) if isinstance(prev_item, dict) else prev_item
            total_prev_oh += prev_val
            pct = cost_service.calc_mom_change(item['unit_cost'], prev_val) if prev_val else None
            if cat in category_map:
                ck, pk, pck, sk = category_map[cat]
                ph[ck] = _money2(item['unit_cost'])
                ph[pk] = _money2(prev_val)
                ph[pck] = self._safe_pct(pct)
                ph[sk] = _change_desc(pct)

        ph['制造费用合计'] = _money2(total_cur_oh)
        ph['上月制造费用合计'] = _money2(total_prev_oh)
        ph['制造费用合计环比'] = self._safe_pct(
            cost_service.calc_mom_change(total_cur_oh, total_prev_oh)
            if total_prev_oh else None)

        # --- 4. 分析文本 ---
        ph['成本异常排查分析'] = anomaly_text
        ph['差异结构拆解分析'] = self._format_breakdown(bench_bd)
        ph['差异归因分析文本'] = bench_text
        ph['本月亮点'] = highlights
        ph['需关注问题'] = issues

        # --- 6. 知识库引用 ---
        ph['配方文档引用'] = recipe_ref
        ph['工艺文档引用'] = process_ref
        ph['GMP文档引用'] = gmp_ref
        ph['行业基准引用'] = industry_ref

        return ph

    # ==================== 工具方法 ====================

    @staticmethod
    def _format_breakdown(bench_bd):
        """格式化差异结构拆解"""
        if 'error' in bench_bd:
            return "暂无差异结构拆解数据"
        total = bench_bd.get('total_diff', 0)
        lines = [f"总单位成本差异: {total:.2f}元/盒"]
        for b in bench_bd.get('breakdown', []):
            lines.append(f"- {b['dimension']}: 差异{b['diff_amount']:.2f}元/盒, "
                         f"贡献度{b['contribution']:.1f}%")
        return "\n".join(lines)


def _change_desc(pct):
    """根据百分比变动生成变动说明"""
    if pct is None:
        return "—"
    if abs(pct) < 1:
        return "基本持平"
    if abs(pct) < 5:
        return f"小幅{'上升' if pct > 0 else '下降'}"
    if abs(pct) < 10:
        return f"较明显{'上升' if pct > 0 else '下降'}"
    return f"显著{'上升' if pct > 0 else '下降'}，需重点关注"
