"""7个动态表格数据生成器 — 供报告引擎调用"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.cost_data import cost_service
from analysis.dashboard import (
    material_detail_table, overhead_detail_table, labor_metrics, cost_trend,
)
from analysis.benchmark import benchmark_diff
from config import ALERT_THRESHOLD


# ===== 辅助 =====

def _pct(val):
    """格式化百分比（保留2位小数）"""
    if val is None:
        return "—"
    return f"{val:.2f}"


def _chg(val):
    """格式化变动率，带↑/↓前缀"""
    if val is None:
        return "—"
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.2f}%"


def _money(val):
    """格式化金额（保留4位小数，适用于单位成本）"""
    if val is None:
        return "—"
    return f"{val:.4f}"


def _money2(val):
    """格式化金额（保留2位小数）"""
    if val is None:
        return "—"
    return f"{val:.2f}"


def _change_reason(pct):
    """根据变动幅度推断变动原因关键词"""
    if pct is None:
        return "—"
    if abs(pct) < 1:
        return "基本持平"
    if abs(pct) < 5:
        return "小幅波动"
    if abs(pct) < 10:
        return "较大波动"
    return "显著变动，需关注"


# ===== 表1: 原材料明细 =====

def build_material_detail_table(product: str, month: str) -> list[list[str]]:
    """
    原材料成本明细表格
    列: 序号 | 原材料名称 | 本月单价(元/盒) | 上月单价(元/盒) | 环比变动 | 变动原因初步判断
    """
    data = material_detail_table(product, month)
    rows = []
    for i, m in enumerate(data['materials'], 1):
        pct = m.get('mom_change')
        rows.append([
            str(i),
            m['material_name'],
            _money(m['unit_cost']),
            _money(m.get('prev_unit_cost', 0)),
            _chg(pct),
            _change_reason(pct),
        ])
    return rows


# ===== 表2: 制造费用明细 =====

def build_overhead_detail_table(product: str, month: str) -> list[list[str]]:
    """
    制造费用明细表格
    列: 费用类别 | 本月(元/盒) | 上月(元/盒) | 环比变动 | 变动说明
    """
    data = overhead_detail_table(product, month)
    rows = []
    for item in data['overheads']:
        pct = item.get('mom_change')
        rows.append([
            item['category'],
            _money(item['unit_cost']),
            _money(item.get('prev_unit_cost', 0)),
            _chg(pct),
            _change_reason(pct),
        ])
    return rows


# ===== 表3: 人工工时 =====

def build_labor_table(product: str, month: str) -> list[list[str]]:
    """
    人工工时分析表格
    列: 指标 | 本月 | 上月 | 环比 | 说明

    行:
      单位人工成本(元/盒)
      人工工时(h/万盒)
      平均小时工资(元/h)
      人工效率(盒/人·日)
    """
    data = labor_metrics(product, month)
    if 'error' in data:
        return []

    metrics = data['metrics']
    rows = []

    labels = [
        ("单位人工成本(元/盒)", "unit_labor_cost"),
        ("人工工时(h/万盒)", "labor_hours_per_10k"),
        ("平均小时工资(元/h)", "avg_hourly_wage"),
        ("人工效率(盒/人·日)", "labor_efficiency"),
    ]

    for label, key in labels:
        m = metrics[key]
        rows.append([
            label,
            _money2(m['current']),
            _money2(m['prev']),
            _chg(m['change']),
            "—",
        ])

    return rows


# ===== 表4: 近6个月趋势 =====

def build_trend_table(product: str) -> list[list[str]]:
    """
    近6个月成本趋势表格
    列: 月份 | 产量(盒) | 单位材料(元/盒) | 单位人工(元/盒) |
        单位制造费用(元/盒) | 单位成本(元/盒) | 环比变动
    """
    data = cost_trend(product)
    rows = []
    for i in range(len(data['months'])):
        month_label = data['months'][i].split('-')[1] + '月'
        prod = data['production'][i]
        mat = data['material'][i]
        lab = data['labor'][i]
        oh = data['overhead'][i]
        uc = data['unit_cost'][i]

        # 环比变动（与上月比较）
        if i > 0:
            prev_uc = data['unit_cost'][i - 1]
            pct = cost_service.calc_mom_change(uc, prev_uc)
            chg_str = _chg(pct)
        else:
            chg_str = "—"

        rows.append([
            month_label,
            f"{prod:,}",
            _money(mat),
            _money(lab),
            _money(oh),
            _money(uc),
            chg_str,
        ])

    return rows


# ===== 表5: 对标差异 =====

def build_benchmark_diff_table(product: str, month: str) -> list[list[str]]:
    """
    对标差异表格（与中药二厂对比）
    列: 对比维度 | 中药一厂 | 中药二厂 | 差异金额 | 差异率 | 方向
    """
    data = benchmark_diff(product, month)
    if 'error' in data:
        return []

    rows = []
    for r in data['rows']:
        rows.append([
            r['dimension'],
            _money2(r['factory1']),
            _money2(r['factory2']),
            _money2(r['diff_amount']),
            _pct(r['diff_rate']) + '%',
            r['direction'],
        ])

    return rows


# ===== 表6: 改进建议 =====

def build_suggestion_table(product: str, month: str, llm_suggestions: str = "") -> list[list[str]]:
    """
    改进建议表格
    列: 序号 | 建议事项 | 责任部门 | 优先级 | 预期效果 | 建议完成时间

    参数:
        llm_suggestions: LLM生成的建议JSON文本，如果为空则生成默认建议
    """
    items = _parse_suggestions_json(llm_suggestions) if llm_suggestions else []

    if not items:
        items = _generate_default_suggestions(product, month)

    rows = []
    for i, item in enumerate(items, 1):
        rows.append([
            str(i),
            item.get('suggestion', ''),
            item.get('department', '财务部'),
            item.get('priority', 'medium'),
            item.get('expected_effect', ''),
            item.get('deadline', ''),
        ])

    return rows


def _parse_suggestions_json(text: str) -> list[dict]:
    """解析LLM返回的建议JSON"""
    try:
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            return json.loads(match.group())
    except (json.JSONDecodeError, Exception):
        pass
    return []


def _generate_default_suggestions(product: str, month: str) -> list[dict]:
    """当LLM不可用时的默认建议"""
    return [
        {
            "suggestion": f"优化{product}原材料采购策略，加强供应商比价管理",
            "department": "采购部",
            "priority": "high",
            "expected_effect": "材料成本降低2%-3%",
            "deadline": f"{month[:5]}{int(month[5:])+1:02d}-28"
        },
        {
            "suggestion": "提高生产线设备利用率，降低单位产品固定成本分摊",
            "department": "生产部",
            "priority": "medium",
            "expected_effect": "制造费用降低1%-2%",
            "deadline": f"{month[:5]}{int(month[5:])+2:02d}-30"
        },
        {
            "suggestion": "优化排班制度，提高劳动生产率",
            "department": "人力资源部",
            "priority": "medium",
            "expected_effect": "人工效率提升5%",
            "deadline": f"{month[:5]}{int(month[5:])+1:02d}-28"
        },
    ]


# ===== 表7: 整改任务 =====

def build_task_table(product: str, month: str, llm_tasks: str = "") -> list[list[str]]:
    """
    整改任务表格
    列: 任务编号 | 任务标题 | 责任人 | 优先级 | 来源 | 截止时间

    参数:
        llm_tasks: LLM返回的任务JSON数组文本
    """
    items = _parse_tasks_json(llm_tasks) if llm_tasks else []

    if not items:
        items = _generate_default_tasks(product, month)

    rows = []
    for item in items:
        assignee = item.get('assignee', {})
        source = item.get('source', {})
        assignee_name = assignee.get('name', '待定') if isinstance(assignee, dict) else str(assignee)
        source_desc = f"{source.get('analysis_type', '月度成本分析')}: {source.get('finding', '')}" \
            if isinstance(source, dict) else str(source)

        rows.append([
            item.get('task_id', ''),
            item.get('task_title', ''),
            assignee_name,
            item.get('priority', 'medium'),
            source_desc[:50],
            item.get('deadline', ''),
        ])

    return rows


def _parse_tasks_json(text: str) -> list[dict]:
    """解析LLM返回的任务JSON"""
    try:
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            return json.loads(match.group())
    except (json.JSONDecodeError, Exception):
        pass
    return []


def _generate_default_tasks(product: str, month: str) -> list[dict]:
    """当LLM不可用时的默认整改任务"""
    year_month = month.replace('-', '')
    return [
        {
            "task_id": f"TASK-{year_month}-0001",
            "task_title": f"{product}原材料采购成本优化专项",
            "assignee": {"name": "张伟", "department": "采购部", "role": "采购经理"},
            "source": {"analysis_type": "月度成本分析", "analysis_month": month,
                        "product": product, "finding": "材料成本高于预算"},
            "priority": "high",
            "deadline": f"{month[:5]}{int(month[5:])+1:02d}-28",
        },
        {
            "task_id": f"TASK-{year_month}-0002",
            "task_title": f"{product}生产线效率提升计划",
            "assignee": {"name": "李强", "department": "生产部", "role": "生产主管"},
            "source": {"analysis_type": "月度成本分析", "analysis_month": month,
                        "product": product, "finding": "人工效率低于行业基准"},
            "priority": "medium",
            "deadline": f"{month[:5]}{int(month[5:])+2:02d}-30",
        },
        {
            "task_id": f"TASK-{year_month}-0003",
            "task_title": "制造费用预算偏差整改",
            "assignee": {"name": "王芳", "department": "财务部", "role": "成本会计"},
            "source": {"analysis_type": "月度成本分析", "analysis_month": month,
                        "product": product, "finding": "制造费用超出预算"},
            "priority": "medium",
            "deadline": f"{month[:5]}{int(month[5:])+1:02d}-15",
        },
    ]
