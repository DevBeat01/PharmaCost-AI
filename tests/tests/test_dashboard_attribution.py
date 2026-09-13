"""成本看板重点分析的回归测试。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))

from analysis.dashboard import _append_rag_sources, _ensure_focus_analysis


def test_short_focus_analysis_is_enriched_without_discarding_llm_reasoning():
    data = {
        "alerts": [{"metric": "直接材料(元/盒)", "change": 12.5}],
        "rows": [{"metric": "直接材料(元/盒)", "current": 6.30, "last_month": 5.60}],
    }
    waterfall = {
        "items": [
            {"name": "直接材料变动", "value": 0.70, "contribution_pct": 72.0},
            {"name": "单位成本变动", "is_total": True},
        ],
    }
    material = {"materials": [{"material_name": "金银花", "unit_cost": 2.10, "mom_change": 9.2}]}
    llm_text = "## 结论摘要\n\n单位成本上涨。\n\n## 重点分析\n\n原材料价格可能上涨。\n\n## 改进建议\n\n1. 核查采购。"

    enriched = _ensure_focus_analysis(llm_text, data, waterfall, material)

    assert "原材料价格可能上涨。" in enriched
    assert "## 改进建议" in enriched
    assert enriched.index("## 结论摘要") < enriched.index("## 重点分析") < enriched.index("## 改进建议")
    assert "本月为6.30，上月为5.60，变动+0.70" in enriched
    assert "1. 核查采购。" in enriched
    assert "执行补充：" not in enriched


def test_detailed_focus_analysis_is_preserved_as_is():
    data = {
        "alerts": [{"metric": "直接材料(元/盒)", "change": 12.5}],
        "rows": [{"metric": "直接材料(元/盒)", "current": 6.30, "last_month": 5.60}],
    }
    detailed_body = (
        "直接材料(元/盒)本月为6.30，上月为5.60，变动+0.70，环比12.50%，贡献72%。\n\n"
        "数据事实：本月单位材料成本高于上月。\n\n"
        "影响判断：该指标触发重点波动，具体原因需进一步核实。\n\n"
        "核查路径：核查采购入库价、合同单价、领料单和单位消耗。\n\n"
        "处置动作：采购部在5个工作日内提交差异核查表及整改措施。"
    )
    text = f"## 重点分析\n\n{detailed_body}\n\n## 改进建议\n\n1. 复核采购。"

    normalized = _ensure_focus_analysis(text, data, {"items": []}, {"materials": []})
    assert normalized.index("## 结论摘要") < normalized.index("## 重点分析") < normalized.index("## 改进建议")
    assert all(label in normalized for label in ("数据事实：", "明细证据：", "影响判断：", "核查路径：", "处置动作："))


def test_substantive_model_focus_is_not_replaced_by_fixed_template():
    data = {
        "product": "六味地黄胶囊", "month": "2026-06",
        "alerts": [{"metric": "直接材料(元/盒)", "change": 12.5}],
        "rows": [{"metric": "直接材料(元/盒)", "current": 6.30, "last_month": 5.60}],
    }
    model_body = (
        "直接材料成本由5.60元/盒升至6.30元/盒，增加0.70元/盒，环比12.50%，"
        "是本次单位成本上涨的主要驱动。金银花采购价上涨与供应商报价变化基本同步，"
        "但领料单显示的单耗和批次收率仍需与工艺记录逐批比对；若收率下降，价格因素之外还会放大材料成本。"
        "建议采购部门先核验入库价与合同价，生产部门同步复核投料量、收率和退料记录，"
        "再按价格差异和用量差异拆分责任，避免把阶段性采购波动误判为长期趋势。"
    )
    text = f"## 结论摘要\n\n材料成本上涨。\n\n## 重点分析\n\n{model_body}\n\n## 改进建议\n\n1. 复核采购入库价。"
    normalized = _ensure_focus_analysis(text, data, {"items": []}, {"materials": []})
    assert model_body in normalized
    assert "归因总览：" not in normalized


def test_model_emphasis_markers_are_preserved_in_attribution_text():
    data = {
        "alerts": [{"metric": "直接材料(元/盒)", "change": 12.5}],
        "rows": [{"metric": "直接材料(元/盒)", "current": 6.30, "last_month": 5.60}],
    }
    model_body = "**直接材料是本次上涨的首要驱动**，采购价与单耗需要继续核查。"
    output = _ensure_focus_analysis(
        f"## 重点分析\n\n{model_body}", data,
        {"items": [{"name": "直接材料变动", "value": 0.7, "contribution_pct": 100}]},
        {"materials": []},
    )
    assert model_body in output


def test_long_but_unstructured_focus_analysis_is_enriched():
    data = {
        "alerts": [{"metric": "直接材料(元/盒)", "change": 12.5}],
        "rows": [{"metric": "直接材料(元/盒)", "current": 6.30, "last_month": 5.60}],
    }
    text = "## 重点分析\n\n" + ("原材料波动需要关注并结合业务情况持续分析。" * 80)
    enriched = _ensure_focus_analysis(text, data, {"items": []}, {"materials": []})
    assert "数据事实：" in enriched
    assert "影响判断：" in enriched
    assert "核查路径：" in enriched
    assert "处置动作：" in enriched


def test_rag_evidence_is_bound_to_source_name():
    output = _append_rag_sources(
        "## 重点分析\n\n结论需进一步核查。",
        ["工艺规程.docx"],
        [{"source": "工艺规程.docx", "content": "提取工艺收率波动需记录并复核。"}],
    )
    assert "## 知识库依据" in output
    assert "[来源：工艺规程.docx] 提取工艺收率波动需记录并复核。" in output


def test_unattributed_legacy_source_section_is_supplemented_with_file_binding():
    output = _append_rag_sources(
        "## 结论摘要\n\n结论。\n\n## 知识库来源\n\n工艺要求需核查。",
        ["工艺规程.docx"],
        [{"source": "工艺规程.docx", "content": "提取工艺收率波动需记录并复核。"}],
    )
    assert "## 知识库来源" in output
    assert "[来源：工艺规程.docx] 提取工艺收率波动需记录并复核。" in output


def test_rag_evidence_is_compact_and_deduplicated_by_source():
    output = _append_rag_sources(
        "## 结论摘要\n\n结论。",
        ["工艺规程.docx", "工艺规程.docx"],
        [
            {"source": "工艺规程.docx", "content": "粉碎收率需达到97%。" + " 这是补充说明。" * 30},
            {"source": "工艺规程.docx", "content": "重复片段不应再次展示。"},
        ],
    )
    source_lines = [line for line in output.splitlines() if line.startswith("- [来源：")]
    assert len(source_lines) == 1
    assert len(source_lines[0]) <= 125


def test_prompt_does_not_treat_rag_timeout_as_attribution_failure():
    from llm.prompts import DASHBOARD_ATTRIBUTION_PROMPT

    assert "没有证据时明确写“需进一步核查”" in DASHBOARD_ATTRIBUTION_PROMPT
    assert "**重点内容**" in DASHBOARD_ATTRIBUTION_PROMPT


def test_attribution_output_budget_allows_full_report():
    import analysis.dashboard as dashboard

    assert dashboard._ATTRIBUTION_MAX_TOKENS >= 4000


def test_stream_consumer_keeps_terminal_event_after_many_deltas(monkeypatch):
    import llm.client as client_module
    from analysis.dashboard import _model_stream_with_timeout

    monkeypatch.setattr(
        client_module.llm_client,
        "chat_stream",
        lambda *args, **kwargs: (f"片段{i}" for i in range(200)),
    )
    chunks = list(_model_stream_with_timeout("prompt", "system", timeout=2, max_tokens=10))
    assert len(chunks) == 200


def test_inline_chapter_text_is_preserved():
    data = {"alerts": [], "rows": []}
    output = _ensure_focus_analysis(
        "## 结论摘要：单位成本总体下降。\n\n## 常规分析：材料价格回落。\n\n## 改进建议\n\n建议复核采购。",
        data, {"items": []}, {"materials": []},
    )
    assert "单位成本总体下降。" in output
    assert "材料价格回落。" in output




def test_total_cost_focus_alert_gets_full_driver_attribution():
    data = {
        "product": "六味地黄胶囊",
        "month": "2026-06",
        "alerts": [{"metric": "总成本(元)", "change": -15.02}],
        "rows": [
            {"metric": "产量(盒)", "current": 35000, "last_month": 40000, "mom_change": -12.5},
            {"metric": "单位成本(元/盒)", "current": 17.57, "last_month": 17.11, "mom_change": 2.69, "budget": 17.10, "budget_deviation": 2.75},
            {"metric": "总成本(元)", "current": 614950, "last_month": 723600, "mom_change": -15.02, "budget": 598500, "budget_deviation": 2.75},
            {"metric": "直接材料(元/盒)", "current": 12.49, "last_month": 12.89, "mom_change": -3.10, "budget": 12.10, "budget_deviation": 3.22},
            {"metric": "直接人工(元/盒)", "current": 2.08, "last_month": 2.12, "mom_change": -1.89, "budget": 2.06, "budget_deviation": 0.97},
            {"metric": "制造费用(元/盒)", "current": 3.00, "last_month": 3.08, "mom_change": -2.60, "budget": 2.94, "budget_deviation": 2.04},
        ],
    }
    waterfall = {"items": [
        {"name": "直接材料变动", "value": -0.40, "contribution_pct": 76.9},
        {"name": "直接人工变动", "value": -0.04, "contribution_pct": 7.7},
        {"name": "制造费用变动", "value": -0.08, "contribution_pct": 15.4},
    ]}
    output = _ensure_focus_analysis(
        "## 重点分析\n\n总成本波动已确认。\n\n数据事实：本月总成本下降。\n\n影响判断：需核查。\n\n核查路径：核查明细。\n\n处置动作：财务部核查。",
        data,
        waterfall,
        {"materials": [{"material_name": "熟地黄", "unit_cost": 3.62, "prev_unit_cost": 3.72, "mom_change": -2.69, "ratio": 29.0}]},
    )
    assert "直接材料(元/盒)" in output
    assert "直接人工(元/盒)" in output
    assert "制造费用(元/盒)" in output
    assert "产量35,000盒" in output
    assert "预算偏差" in output


def test_numeric_chapters_are_normalized_and_suggestions_are_actionable():
    data = {
        "product": "六味地黄胶囊", "month": "2026-06",
        "alerts": [{"metric": "总成本(元)", "change": -15.02}],
        "rows": [{"metric": "直接材料(元/盒)", "current": 12.49, "last_month": 12.89}],
    }
    text = (
        "1. 结论摘要\n\n单位成本下降。\n\n"
        "2. 重点分析\n\n数据核验与处置要点\n\n分析很简单。\n\n"
        "3. 改进建议\n\n1. 持续关注成本。\n\n"
        "知识库来源：\n- 工艺文档.pdf"
    )
    output = _ensure_focus_analysis(
        text,
        data,
        {"items": [{"name": "直接材料变动", "value": 0.2, "contribution_pct": 100}]},
        {"materials": []},
    )
    assert output.count("## 结论摘要") == 1
    assert output.count("## 重点分析") == 1
    assert output.count("## 改进建议") == 1
    assert output.count("## 知识库依据") == 1
    assert "采购部核查主要药材采购入库价" in output
    assert "持续关注成本" not in output


def test_unstructured_model_output_is_not_discarded():
    data = {
        "product": "六味地黄胶囊", "month": "2026-06",
        "alerts": [],
        "rows": [{"metric": "单位成本(元/盒)", "current": 17.57, "last_month": 18.09, "mom_change": -2.87}],
    }
    output = _ensure_focus_analysis(
        "本月单位成本较上月下降，主要成本要素未触发重点波动阈值。",
        data,
        {"items": []},
        {"materials": []},
    )
    assert "本月单位成本较上月下降" in output
    assert "## 常规分析" in output


def test_model_suggestions_are_preserved_without_forced_template():
    data = {"month": "2026-06"}
    waterfall = {"items": [{"name": "直接材料变动", "value": 0.2}]}
    output = _ensure_focus_analysis(
        "## 结论摘要\n\n材料上涨。\n\n## 重点分析\n\n采购价格需要核查。\n\n"
        "## 改进建议\n\n建议采购先核对合同价与入库价。",
        {**data, "alerts": [{"metric": "直接材料(元/盒)", "change": 12}], "rows": []},
        waterfall,
        {"materials": []},
    )
    assert "建议采购先核对合同价与入库价。" in output
    assert "执行补充：" not in output
    assert "采购部核查主要药材采购入库价" not in output


def test_inline_suggestion_after_heading_is_preserved():
    data = {"product": "六味地黄胶囊", "month": "2026-06", "alerts": [], "rows": []}
    output = _ensure_focus_analysis(
        "## 结论摘要\n\n成本稳定。\n\n## 常规分析\n\n无异常。\n\n"
        "## 改进建议：建议复核采购价与预算差异。",
        data, {"items": []}, {"materials": []},
    )
    assert "建议复核采购价与预算差异。" in output
    assert "采购部核查主要药材采购入库价" not in output
