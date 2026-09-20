"""RPA整改任务生成、选择派发和状态追踪客户端。"""
import asyncio
import httpx
import json
import logging
import re
import sys
import uuid
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import RPA_BASE_URL, RPA_TASK_DB_PATH
from rpa.task_store import TaskStore

logger = logging.getLogger("rpa.client")

ASSIGNEE_MAP = {
    "材料": {"name": "张伟", "department": "采购部", "role": "采购经理"},
    "人工": {"name": "李芳", "department": "生产部", "role": "生产主管"},
    "制造费用": {"name": "王强", "department": "设备部", "role": "设备经理"},
    "效率": {"name": "李芳", "department": "生产部", "role": "生产主管"},
    "通用": {"name": "陈明", "department": "财务部", "role": "成本会计"},
}
RPA_STATUSES = {"draft", "sent", "received", "confirmed", "in_progress", "completed", "overdue", "failed"}
SCENARIO_NAMES = {
    "dashboard_attribution": "成本看板归因分析",
    "benchmark_attribution": "成本对标归因分析",
    "report_generated_tasks": "报告整改任务清单",
}

# 任务标题必须能落到可核查的业务对象，避免把“加强管理”等口号直接派发。
_TASK_ACTION_RE = re.compile(r"(核查|复核|比对|检查|确认|提取|调取|汇总|分析|制定|建立|落实|整改|补充|验证|跟进|优化|共享|清理)")
_TASK_OBJECT_RE = re.compile(
    r"(采购|原材料|药材|供应商|报价|合同|入库价|领料单|投料|单耗|收率|工时|排班|加班|人员|"
    r"设备|能耗|维修|费用分摊|分摊表|成本明细|会计凭证|费用科目|产量|工艺参数|运行记录)"
)
_TASK_VAGUE_RE = re.compile(r"(加强管理|持续关注|密切关注|提高意识|优化成本|降本增效|提升能力|协同推进|做好.*工作)")
_TASK_RESULT_RE = re.compile(r"(差异核查表|核查表|复核记录|确认记录|改善方案|整改措施|汇总表|对比表|签字记录)")

# 任务草稿与派发状态持久化到 SQLite，应用重启后仍可继续处理。
TASK_STORE = TaskStore(RPA_TASK_DB_PATH)


def _unwrap_response(payload: dict) -> dict:
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload


def _deadline_for_month(month: str, days: int = 30) -> str:
    """为历史分析也生成尚未逾期、可执行的截止日。"""
    report_deadline = (datetime.strptime(month + "-28", "%Y-%m-%d") + timedelta(days=days)).date()
    earliest_deadline = (datetime.now().date() + timedelta(days=7))
    return max(report_deadline, earliest_deadline).isoformat()


def _determine_assignee(text: str) -> dict:
    for key, assignee in ASSIGNEE_MAP.items():
        if key in text:
            return assignee.copy()
    return ASSIGNEE_MAP["通用"].copy()


def _normalize_priority(value: str) -> str:
    aliases = {"高": "high", "中": "medium", "低": "low"}
    value = aliases.get(str(value).strip(), str(value).strip().lower())
    return value if value in {"high", "medium", "low"} else "medium"


def _valid_deadline(value: object, month: str) -> str:
    """保留近期有效日期；模型给出历史或过远日期时使用系统截止日。"""
    try:
        deadline = datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
        today = datetime.now().date()
        if today <= deadline <= today + timedelta(days=90):
            return deadline.isoformat()
    except (TypeError, ValueError):
        pass
    return _deadline_for_month(month)


def _is_executable_task(item: dict) -> bool:
    """判断任务是否具备动作、对象和可验收交付物。"""
    if not isinstance(item, dict):
        return False
    title = re.sub(r"\s+", "", str(item.get("task_title") or ""))
    if len(title) < 8 or _TASK_VAGUE_RE.search(title):
        return False
    expected_result = re.sub(r"\s+", "", str(item.get("expected_result") or item.get("suggestion") or ""))
    return bool(
        _TASK_ACTION_RE.search(title)
        and _TASK_OBJECT_RE.search(title)
        and len(expected_result) >= 8
        and not _TASK_VAGUE_RE.search(expected_result)
        and _TASK_RESULT_RE.search(expected_result)
    )


def _default_expected_result(title: str) -> str:
    """为模型遗漏交付物的任务补齐可审核的最小完成标准。"""
    if any(word in title for word in ("采购", "合同", "供应商", "入库价", "原材料", "药材")):
        return "输出采购入库价、合同单价及报价差异核查表，并提交整改措施。"
    if any(word in title for word in ("人工", "工时", "排班", "人员", "效率", "单耗", "收率", "工艺")):
        return "输出工时、单耗或收率复核记录，并提交改善方案。"
    if any(word in title for word in ("设备", "能耗", "维修", "费用", "分摊", "产量")):
        return "输出费用明细与分摊差异核查表，并提交整改措施。"
    return "输出成本明细核查表，并提交经责任部门确认的整改措施。"


def _normalize_task_instruction(item: dict) -> dict:
    """统一任务标题和验收交付物，确保草稿可直接执行和验收。"""
    normalized = dict(item or {})
    title = re.sub(r"\s+", " ", str(normalized.get("task_title") or "")).strip().rstrip("。；")
    # Keep the dispatch card scannable even when the model echoes the whole
    # attribution paragraph instead of a concise task title.
    normalized["task_title"] = title[:120]
    expected_result = re.sub(r"\s+", " ", str(
        normalized.get("expected_result") or normalized.get("suggestion") or ""
    )).strip().rstrip("。；")
    if not _TASK_RESULT_RE.search(expected_result) or _TASK_VAGUE_RE.search(expected_result):
        expected_result = _default_expected_result(normalized["task_title"])
    normalized["expected_result"] = expected_result[:200]
    # suggestion 是既有 RPA 协议字段，继续使用它携带验收交付物。
    normalized["suggestion"] = expected_result
    return normalized


def _filter_executable_tasks(
    items: list[dict], month: str, max_tasks: int = 3,
) -> tuple[list[dict], int]:
    """清理模型输出，并将截止日规范为可执行的近期日期。"""
    accepted, seen, rejected = [], set(), 0
    for item in items:
        normalized = _normalize_task_instruction(item)
        if not _is_executable_task(normalized):
            rejected += 1
            continue
        key = re.sub(r"\s+", "", normalized["task_title"])
        if key in seen:
            continue
        seen.add(key)
        normalized["deadline"] = _valid_deadline(item.get("deadline"), month)
        accepted.append(normalized)
        if len(accepted) >= max(1, max_tasks):
            break
    return accepted, rejected


def _normalize_report_task_candidates(
    items: list[dict], month: str, max_tasks: int = 10,
) -> tuple[list[dict], list[str]]:
    """保真转换报告清单，避免通用关键词词库误删已展示的报告任务。"""
    accepted, rejected, seen_ids = [], [], set()
    for index, item in enumerate((items or [])[:max_tasks], 1):
        if not isinstance(item, dict):
            rejected.append(f"第{index}项不是任务对象")
            continue
        task_id = str(item.get("task_id") or "").strip()
        if task_id and task_id in seen_ids:
            rejected.append(f"第{index}项任务编号重复")
            continue
        if task_id:
            seen_ids.add(task_id)
        normalized = _normalize_task_instruction(item)
        if len(normalized.get("task_title") or "") < 2:
            rejected.append(f"第{index}项任务标题为空")
            continue
        normalized["deadline"] = _valid_deadline(item.get("deadline"), month)
        accepted.append(normalized)
    return accepted, rejected


async def _request(method: str, path: str, **kwargs) -> dict:
    last_error = None
    for attempt in range(3):
        try:
            # RPA 是本机服务，连接失败应快速反馈；过长的默认超时会让前端
            # 在后端重试完成前先超时，用户看不到真实原因。
            timeout = httpx.Timeout(connect=3.0, read=12.0, write=5.0, pool=3.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.request(method, f"{RPA_BASE_URL.rstrip('/')}{path}", **kwargs)
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, dict) and payload.get("code") not in (None, 200):
                    raise RuntimeError(payload.get("message") or f"RPA业务错误: {payload.get('code')}")
                return payload
        except httpx.HTTPStatusError as exc:
            detail = ""
            try:
                body = exc.response.json()
                detail = body.get("detail") or body.get("message") or ""
            except (ValueError, AttributeError):
                pass
            last_error = RuntimeError(
                f"RPA接口返回 HTTP {exc.response.status_code}"
                + (f"：{detail}" if detail else "")
            )
            if attempt < 2:
                await asyncio.sleep(0.25 * (2 ** attempt))
        except (httpx.TimeoutException, httpx.NetworkError, RuntimeError) as exc:
            last_error = exc
            if attempt < 2:
                await asyncio.sleep(0.25 * (2 ** attempt))
    raise last_error or RuntimeError("RPA请求失败")


def _dispatch_error_message(exc: Exception) -> str:
    """将底层网络异常转换为前端可直接展示的处理提示。"""
    if isinstance(exc, (httpx.ConnectError, httpx.NetworkError)):
        return f"RPA服务未启动或无法连接（{RPA_BASE_URL.rstrip('/')}），请运行 start_rpa.bat 或 start_all.bat"
    if isinstance(exc, httpx.TimeoutException):
        return "RPA服务响应超时，请检查 RPA 服务窗口后重试"
    return str(exc) or "RPA请求失败，请检查服务状态"


async def send_rpa_task(task_data: dict) -> dict:
    return await _request("POST", "/api/rpa/tasks", json=task_data)


async def send_wechat_notify(recipient: str, department: str, message: str) -> dict:
    return _unwrap_response(await _request("POST", "/api/notify/wechat", json={
        "recipient": recipient,
        "department": department,
        "message": message,
    }))


def _parse_llm_tasks(raw_text: str) -> list[dict]:
    try:
        match = re.search(r"\[.*\]", str(raw_text or ""), re.DOTALL)
        items = json.loads(match.group()) if match else []
        result = []
        seen = set()
        for item in items:
            if not isinstance(item, dict) or not item.get("task_title"):
                continue
            title = re.sub(r"\s+", "", str(item["task_title"]))
            if title in seen:
                continue
            seen.add(title)
            result.append(item)
        return result[:3]
    except (json.JSONDecodeError, TypeError, AttributeError):
        return []


async def _generate_llm_tasks(
    product: str, month: str, scenario: str, conclusion: str, evidence: dict,
) -> list[dict]:
    from llm.client import llm_client
    from llm.prompts import TASK_GENERATION_PROMPT

    evidence_text = json.dumps(evidence or {}, ensure_ascii=False, default=str)
    prompt = TASK_GENERATION_PROMPT.format(
        context=f"分析场景：{scenario}\n归因结论：{conclusion}\n分析证据：{evidence_text}",
        product=product,
        month=month,
    )
    raw = await asyncio.to_thread(llm_client.chat, prompt, max_tokens=1200)
    return _parse_llm_tasks(raw)


def _fallback_tasks(product: str, month: str, scenario: str, conclusion: str, evidence: dict) -> list[dict]:
    """模型不可用时，依据当前分析页提交的结论和证据生成可追溯草稿。"""
    source_text = f"{conclusion} {json.dumps(evidence or {}, ensure_ascii=False, default=str)}"
    month_label = month.replace("-", "年") + "月"
    priority = "high" if any(word in source_text for word in ("异常", "超", "高于", "上涨", "差异")) else "medium"
    # 模型不可用时优先使用归因文本中的编号/项目建议，避免每次都返回相同标题。
    extracted = []
    for line in conclusion.splitlines():
        match = re.match(r"^\s*(?:[-*•]|\d+[.)、])\s*(.+)$", line)
        if match and len(match.group(1).strip()) >= 8:
            value = re.sub(r"[*_`]+", "", match.group(1)).strip()
            if value not in extracted:
                extracted.append(value)
    if extracted:
        tasks = []
        for suggestion in extracted[:3]:
            dimension = "材料" if any(k in suggestion for k in ("采购", "原材料", "供应商", "合同", "价格")) else ("人工" if any(k in suggestion for k in ("人工", "工时", "生产", "效率")) else "制造费用")
            tasks.append({
                "task_title": suggestion[:100],
                "assignee": _determine_assignee(dimension),
                "source": {"analysis_scenario": scenario, "attribution_conclusion": conclusion},
                "priority": priority,
                "deadline": _deadline_for_month(month),
                "suggestion": suggestion,
            })
        executable, _ = _filter_executable_tasks(tasks, month)
        if executable:
            return executable
    candidates = [
        (
            "材料", ("材料", "采购", "原材料", "供应商", "合同", "价格"),
            f"请核查{product}{month_label}原材料采购价格、合同调价条款及供应商报价",
            "复核采购价格、合同条款和供应商报价，形成材料成本整改措施。",
        ),
        (
            "人工", ("人工", "工时", "生产", "工艺", "效率", "单耗", "收率"),
            f"请复核{product}{month_label}生产工时、单耗及工艺执行情况",
            "核查生产工时、单耗和工艺参数，制定效率改善措施。",
        ),
        (
            "制造费用", ("制造费用", "设备", "能耗", "产量", "分摊"),
            f"请核查{product}{month_label}设备费用、能耗及产量分摊情况",
            "核查设备利用率、能耗和费用分摊，制定降本措施。",
        ),
    ]
    tasks = []
    for dimension, keywords, title, suggestion in candidates:
        if any(keyword in source_text for keyword in keywords):
            tasks.append({
                "task_title": title,
                "assignee": _determine_assignee(dimension),
                "source": {"analysis_scenario": scenario, "attribution_conclusion": conclusion},
                "priority": priority,
                "deadline": _deadline_for_month(month),
                "suggestion": suggestion,
            })
    return tasks[:3] or [{
        "task_title": f"请复核{product}{month_label}成本明细、会计凭证及费用分摊表",
        "assignee": _determine_assignee("通用"),
        "source": {"analysis_scenario": scenario, "attribution_conclusion": conclusion},
        "priority": priority,
        "deadline": _deadline_for_month(month),
        "suggestion": "核对成本明细、会计凭证和费用分摊表，形成可验证的差异核查记录。",
    }]


def _benchmark_suggestion_tasks(product: str, month: str, scenario: str, conclusion: str, evidence: dict) -> list[dict]:
    """对标场景只使用已校验的结构化建议，禁止从长归因正文自由拆分任务。"""
    suggestions = evidence.get("improvement_suggestions", []) if isinstance(evidence, dict) else []
    tasks = []
    seen = set()
    for item in suggestions:
        if not isinstance(item, dict):
            continue
        suggestion = str(item.get("suggestion") or "").strip()
        normalized = re.sub(r"\s+", "", suggestion)
        if len(suggestion) < 8 or normalized in seen:
            continue
        seen.add(normalized)
        department = str(item.get("department") or "")
        dimension = str(item.get("source_dimension") or department or "成本差异")
        assignee_key = "材料" if "采购" in department else ("人工" if "生产" in department else "制造费用")
        title = re.sub(r"\s+", " ", suggestion).strip().rstrip("。；")
        if not re.match(r"^(请|核查|复核|建立|制定|落实|启动|开展|优化|共享|确认|加强)", title):
            title = f"请落实：{title}"
        candidate = {
            "task_title": title[:100],
            "assignee": _determine_assignee(assignee_key),
            "source": {"analysis_scenario": scenario, "attribution_conclusion": conclusion},
            "priority": _normalize_priority(item.get("priority", "medium")),
            "deadline": item.get("deadline") or _deadline_for_month(month),
            "suggestion": suggestion,
        }
        candidate = _normalize_task_instruction(candidate)
        if _is_executable_task(candidate):
            candidate["deadline"] = _valid_deadline(candidate["deadline"], month)
            tasks.append(candidate)
        if len(tasks) == 3:
            break
    return tasks


def _make_task_draft(
    product: str, month: str, item: dict, scenario: str, conclusion: str, evidence: dict | None = None,
) -> dict:
    item = _normalize_task_instruction(item)
    source = item.get("source") if isinstance(item.get("source"), dict) else {}
    source_text = " ".join(str(value) for value in source.values())
    assignee = item.get("assignee") if isinstance(item.get("assignee"), dict) else {}
    if not assignee.get("department") or not assignee.get("role"):
        assignee = _determine_assignee(source_text or item.get("task_title", ""))
    else:
        assignee = {
            "name": assignee.get("name") or _determine_assignee(assignee["department"])["name"],
            "department": assignee["department"],
            "role": assignee["role"],
        }
    source_payload = {
        "analysis_scenario": source.get("analysis_scenario") or scenario,
        "attribution_conclusion": source.get("attribution_conclusion") or conclusion,
    }
    # 报告任务的原始编号和单项结论用于追溯，但不替换系统生成的草稿编号。
    for key in ("report_task_id", "report_source_conclusion"):
        if str(source.get(key) or "").strip():
            source_payload[key] = str(source[key]).strip()
    return {
        "task_id": f"TASK-{month}-{uuid.uuid4().hex[:10].upper()}",
        "task_title": item.get("task_title") or f"请检查{product}成本异常原因",
        "assignee": assignee,
        "source": source_payload,
        "priority": _normalize_priority(item.get("priority", "medium")),
        "deadline": _valid_deadline(item.get("deadline"), month),
        "suggestion": item.get("suggestion", ""),
        "expected_result": item.get("expected_result", ""),
        "analysis_evidence": dict(evidence or {}),
        "rag_used": bool(evidence.get("rag_sources") or evidence.get("rag_used")) if isinstance(evidence, dict) else False,
        "rag_sources": list(evidence.get("rag_sources") or []) if isinstance(evidence, dict) else [],
        "product": product,
        "month": month,
        "status": "draft",
        "notification_status": "not_sent",
        "created_at": datetime.now().isoformat(),
    }


async def generate_task_drafts(
    product: str,
    month: str,
    analysis_scenario: str,
    attribution_conclusion: str,
    analysis_evidence: dict | None = None,
    prebuilt_tasks: list[dict] | None = None,
) -> dict:
    """根据分析页已展示的归因结论生成临时任务，不重新执行分析、不触发RPA。"""
    scenario = SCENARIO_NAMES[analysis_scenario]
    conclusion = attribution_conclusion.strip()
    evidence = dict(analysis_evidence or {})
    # 报告入口只能消费本次报告已经生成并展示的任务清单。这里故意不调用
    # LLM、也不从报告长文本或规则重新推导，确保用户审阅的是报告原有结论。
    if analysis_scenario == "report_generated_tasks":
        submitted_tasks = len(prebuilt_tasks or [])
        # 报告 Word 表和预览中的清单已经是用户可见的唯一任务依据。这里仅做
        # 结构、编号和基础字段校验，不再套用看板/对标专用的关键词白名单。
        generated, rejection_reasons = _normalize_report_task_candidates(
            prebuilt_tasks or [], month, max_tasks=10,
        )
        rejected_tasks = len(rejection_reasons)
        if not generated:
            raise ValueError("报告整改任务清单中没有有效的结构化任务")
        for item in generated:
            assignee = item.get("assignee") if isinstance(item.get("assignee"), dict) else {}
            if not all(str(assignee.get(key) or "").strip() for key in ("name", "department", "role")):
                item["assignee"] = _determine_assignee(str(item.get("task_title") or ""))
            raw_source = item.get("source") if isinstance(item.get("source"), dict) else {}
            item["source"] = {
                "analysis_scenario": scenario,
                "attribution_conclusion": conclusion,
                "report_task_id": str(item.get("task_id") or "").strip(),
                "report_source_conclusion": str(
                    raw_source.get("finding") or raw_source.get("attribution_conclusion") or ""
                ).strip(),
            }
        drafts = [_make_task_draft(product, month, item, scenario, conclusion, evidence) for item in generated]
        return {
            "product": product, "month": month, "submitted_tasks": submitted_tasks,
            "tasks_generated": len(drafts), "generation_source": "report_task_candidates",
            "rejected_tasks": rejected_tasks, "rejection_reasons": rejection_reasons,
            "tasks": drafts,
        }

    # 两类分析场景都优先调用 AI；结构化建议仅作为模型不可用时的备用来源。
    generated = []
    rejected_tasks = 0
    generation_source = "ai"
    if not generated:
        try:
            generated = await _generate_llm_tasks(product, month, scenario, conclusion, evidence)
            generated, rejected_tasks = _filter_executable_tasks(generated, month)
        except Exception:
            logger.warning("LLM整改任务JSON生成失败，使用规则化兜底任务", exc_info=True)
            generated = []
    if not generated:
        if analysis_scenario == "benchmark_attribution":
            generated = _benchmark_suggestion_tasks(product, month, scenario, conclusion, evidence)
            generated, rejected = _filter_executable_tasks(generated, month)
            rejected_tasks += rejected
            if generated:
                generation_source = "analysis_suggestions"
        if not generated:
            generation_source = "fallback"
            generated = _fallback_tasks(product, month, scenario, conclusion, evidence)
            generated, rejected = _filter_executable_tasks(generated, month)
            rejected_tasks += rejected

    # 这里只生成临时草稿供分析页审阅，不写入持久化任务表。
    # 模型不得决定实际责任人；草稿统一落到系统已配置的部门和岗位。
    for item in generated:
        item["assignee"] = _determine_assignee(str(item.get("task_title") or ""))
    drafts = [_make_task_draft(product, month, item, scenario, conclusion, evidence) for item in generated[:3]]
    return {
        "product": product, "month": month, "tasks_generated": len(drafts),
        "generation_source": generation_source, "rejected_tasks": rejected_tasks, "tasks": drafts,
    }


async def save_selected_task_drafts(tasks: list[dict]) -> dict:
    """仅保存用户在审阅弹窗中明确勾选的任务草稿。"""
    saved = []
    report_task_ids = set()
    for item in tasks[:20]:
        if not isinstance(item, dict):
            continue
        product = str(item.get("product") or "").strip()
        month = str(item.get("month") or "").strip()
        source = item.get("source") if isinstance(item.get("source"), dict) else {}
        scenario = str(source.get("analysis_scenario") or "成本看板归因分析").strip()
        conclusion = str(source.get("attribution_conclusion") or "").strip()
        if not product or not month or not conclusion or not item.get("task_title"):
            continue
        item = _normalize_task_instruction(item)
        is_report_task = scenario == SCENARIO_NAMES["report_generated_tasks"]
        report_task_id = str(source.get("report_task_id") or "").strip()
        if is_report_task and report_task_id in report_task_ids:
            logger.warning("拒绝保存重复的报告整改任务: %s", report_task_id)
            continue
        if is_report_task:
            if len(item.get("task_title") or "") < 2:
                logger.warning("拒绝保存空标题的报告整改任务")
                continue
            if report_task_id:
                report_task_ids.add(report_task_id)
        elif not _is_executable_task(item):
            logger.warning("拒绝保存不可执行的整改任务: %s", item.get("task_title"))
            continue
        draft = _make_task_draft(
            product, month, item, scenario, conclusion,
            item.get("analysis_evidence") if isinstance(item.get("analysis_evidence"), dict) else {},
        )
        saved.append(draft)
    TASK_STORE.upsert_many(saved)
    return {"tasks_saved": len(saved), "tasks": saved}


def _rpa_payload(task: dict) -> dict:
    return {
        "task_id": task["task_id"],
        "task_title": task["task_title"],
        "assignee": task["assignee"],
        "source": {
            "analysis_type": task["source"]["analysis_scenario"],
            "analysis_month": task["month"],
            "product": task["product"],
            "finding": task["source"]["attribution_conclusion"],
            "evidence": task.get("analysis_evidence", {}),
        },
        "priority": task["priority"],
        "deadline": task["deadline"],
        "suggestion": task["suggestion"],
        "notify_method": "wechat",
        "created_at": task["created_at"],
    }


async def dispatch_selected_tasks(task_ids: list[str]) -> dict:
    """仅将前端勾选的草稿任务发送到RPA，不发送微信。"""
    # 兼容旧版本留下的 failed 状态，允许直接重新派发。
    _restore_failed_dispatch_tasks()
    results = []
    for task_id in dict.fromkeys(task_ids):
        task = TASK_STORE.get(task_id)
        if not task:
            results.append({"task_id": task_id, "status": "failed", "error": "任务不存在"})
            continue
        if task["status"] != "draft":
            results.append({"task_id": task_id, "status": "failed", "error": "仅待派发任务可发送"})
            continue
        try:
            rpa_result = await send_rpa_task(_rpa_payload(task))
            task["status"] = "sent"
            task["notification_status"] = "not_sent"
            task["rpa_result"] = _unwrap_response(rpa_result)
            TASK_STORE.upsert(task)
            results.append({
                "task_id": task_id,
                "status": "sent",
                "rpa_status": "已发送至 RPA",
            })
        except Exception as exc:
            # 派发未成功的任务一律回到待派发队列，方便用户修正后再次发送。
            logger.warning("RPA任务派发未成功，任务保留待派发: %s", exc)
            task["status"] = "draft"
            task["notification_status"] = "not_sent"
            TASK_STORE.upsert(task)
            results.append({
                "task_id": task_id,
                "status": "draft",
                "error": f"{_dispatch_error_message(exc)}；任务已恢复为待派发，请检查后重试",
                "retryable": True,
            })

    sent = sum(item["status"] == "sent" for item in results)
    return {"tasks_dispatched": sent, "tasks_failed": len(results) - sent, "results": results}


async def notify_selected_tasks(task_ids: list[str]) -> dict:
    """将已进入RPA且尚未通知的任务发送微信。"""
    results = []
    for task_id in dict.fromkeys(task_ids):
        task = TASK_STORE.get(task_id)
        if not task:
            results.append({"task_id": task_id, "status": "failed", "error": "任务不存在"})
            continue
        if task.get("status") == "draft":
            results.append({"task_id": task_id, "status": "failed", "error": "请先发送至RPA"})
            continue
        if task.get("notification_status") == "sent":
            results.append({"task_id": task_id, "status": "failed", "error": "微信已发送"})
            continue
        try:
            notification = await send_wechat_notify(
                task["assignee"]["name"], task["assignee"]["department"],
                f"【成本整改任务】\n任务：{task['task_title']}\n优先级：{task['priority']}\n"
                f"归因结论：{task['source']['attribution_conclusion']}\n截止：{task['deadline']}",
            )
            task["notification_status"] = "sent"
            task["notification"] = notification
            TASK_STORE.upsert(task)
            results.append({
                "task_id": task_id,
                "status": "sent",
                "notify_status": {"wechat": f"已发送至 {task['assignee']['name']}({task['assignee']['department']})"},
            })
        except Exception as exc:
            logger.exception("微信通知失败")
            task["notification_status"] = "failed"
            TASK_STORE.upsert(task)
            results.append({"task_id": task_id, "status": "failed", "error": str(exc)})

    sent = sum(item["status"] == "sent" for item in results)
    return {"notifications_sent": sent, "notifications_failed": len(results) - sent, "results": results}


async def _refresh_task_statuses() -> None:
    sent_tasks = [task for task in TASK_STORE.list() if task["status"] in RPA_STATUSES - {"draft", "failed"}]
    if not sent_tasks:
        return

    async def refresh_one(task: dict) -> None:
        try:
            # 状态刷新不能阻塞任务列表；RPA 服务不可用时保留本地状态。
            remote = _unwrap_response(await asyncio.wait_for(
                _request("GET", f"/api/rpa/tasks/{task['task_id']}"), timeout=3,
            ))
            task["status"] = remote.get("status", task["status"])
            TASK_STORE.upsert(task)
        except Exception:
            logger.debug("RPA状态刷新失败: %s", task["task_id"], exc_info=True)

    await asyncio.gather(*(refresh_one(task) for task in sent_tasks))


def _restore_failed_dispatch_tasks() -> None:
    """将旧版本留下的派发失败状态迁移回可重试的待派发状态。"""
    for task in TASK_STORE.list(status="failed"):
        task["status"] = "draft"
        task["notification_status"] = "not_sent"
        TASK_STORE.upsert(task)


async def list_rpa_tasks(**filters) -> dict:
    # 列表优先读取本地 SQLite，避免远端逐条状态查询导致页面超时。
    # 派发、通知和单条详情仍会同步保存最新状态。
    # 兼容旧版本将派发异常写为 failed 的记录：恢复为待派发，支持再次发送。
    _restore_failed_dispatch_tasks()
    if filters.get("status") == "failed":
        filters = {**filters, "status": "draft"}
    tasks = TASK_STORE.list(**filters)
    return {"total": len(tasks), "tasks": tasks}


async def get_rpa_task(task_id: str) -> dict:
    await _refresh_task_statuses()
    return TASK_STORE.get(task_id) or _unwrap_response(await _request("GET", f"/api/rpa/tasks/{task_id}"))


async def get_rpa_stats() -> dict:
    _restore_failed_dispatch_tasks()
    tasks = TASK_STORE.list()
    counts = Counter(task["status"] for task in tasks)
    total = len(tasks)
    delivered_statuses = ("received", "confirmed", "in_progress", "completed", "overdue")
    confirmed_statuses = ("confirmed", "in_progress", "completed", "overdue")
    return {
        "total": total,
        "generated": total,
        "draft": counts["draft"],
        "dispatched": total - counts["draft"],
        "delivered": sum(counts[status] for status in delivered_statuses),
        "confirmed": sum(counts[status] for status in confirmed_statuses),
        "processing": counts["in_progress"],
        "completed": counts["completed"],
        "failed": counts["failed"],
        "by_status": dict(counts),
    }


async def delete_rpa_task(task_id: str) -> dict:
    """删除本地持久化的整改任务及其闭环记录。"""
    task = TASK_STORE.get(task_id)
    if not task:
        raise KeyError("任务不存在")
    TASK_STORE.delete(task_id)
    return {"task_id": task_id, "deleted": True, "status": task.get("status")}


async def update_rpa_task(task_id: str, updates: dict) -> dict:
    """更新待派发草稿，派发后的任务不可篡改。"""
    task = TASK_STORE.get(task_id)
    if not task:
        raise KeyError("任务不存在")
    if task.get("status") != "draft":
        raise ValueError("仅待派发任务可修改")

    if "task_title" in updates:
        task["task_title"] = str(updates["task_title"]).strip()
    if "assignee" in updates:
        assignee = updates["assignee"] if isinstance(updates["assignee"], dict) else {}
        department = str(assignee.get("department") or "").strip()
        role = str(assignee.get("role") or "").strip()
        if not department or not role:
            raise ValueError("责任部门和岗位不能为空")
        task["assignee"] = {
            "name": str(assignee.get("name") or task["assignee"].get("name") or "责任人").strip(),
            "department": department,
            "role": role,
        }
    if "priority" in updates:
        task["priority"] = _normalize_priority(updates["priority"])
    if "deadline" in updates:
        deadline = str(updates["deadline"]).strip()
        try:
            datetime.strptime(deadline, "%Y-%m-%d")
        except ValueError:
            raise ValueError("截止时间格式应为 YYYY-MM-DD")
        task["deadline"] = deadline
    if "suggestion" in updates:
        task["suggestion"] = str(updates["suggestion"]).strip()
    task["updated_at"] = datetime.now().isoformat()
    TASK_STORE.upsert(task)
    return task
