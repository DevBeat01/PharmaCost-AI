"""RPA整改任务生成、勾选派发与消息推送回归测试。"""
import asyncio
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))

from data.cost_data import cost_service
from rpa import client
from rpa.task_store import TaskStore


def with_temporary_store(callback):
    original_store = client.TASK_STORE
    with tempfile.TemporaryDirectory() as directory:
        client.TASK_STORE = TaskStore(Path(directory) / "tasks.sqlite3")
        try:
            return callback(Path(directory) / "tasks.sqlite3")
        finally:
            client.TASK_STORE = original_store


def test_task_draft_has_required_business_fields():
    task = client._make_task_draft(
        "银黄口服液", "2026-06",
        {
            "task_title": "请检查金银花2026年6月采购合同调价条款",
            "assignee": {"name": "张伟", "department": "采购部", "role": "采购经理"},
            "source": {"analysis_scenario": "成本对标分析", "attribution_conclusion": "金银花采购成本高于基准"},
            "priority": "高",
            "deadline": "2026-07-15",
        },
        "成本对标归因分析", "金银花采购成本高于基准",
        {"benchmark_differences": [{"dimension": "直接材料", "diff_amount": 0.2}]},
    )
    assert {"task_title", "assignee", "source", "priority", "deadline"} <= set(task)
    assert {"department", "role"} <= set(task["assignee"])
    assert {"analysis_scenario", "attribution_conclusion"} <= set(task["source"])
    assert task["status"] == "draft"
    assert task["priority"] == "high"
    assert task["analysis_evidence"]["benchmark_differences"][0]["dimension"] == "直接材料"


def test_drafts_use_the_submitted_dashboard_and_benchmark_attribution_context():
    async def generate_with_fallback():
        with patch.object(client, "_generate_llm_tasks", new_callable=AsyncMock, return_value=[]):
            dashboard = await client.generate_task_drafts(
                "银黄口服液", "2026-06", "dashboard_attribution",
                "直接材料成本环比上涨，主要原材料采购价格异常。",
                {"alerts": [{"metric": "直接材料", "change": 12.5}]},
            )
            benchmark = await client.generate_task_drafts(
                "银黄口服液", "2026-06", "benchmark_attribution",
                "中药一厂直接人工成本高于中药二厂，需要复核工时效率。",
                {"benchmark_differences": [{"dimension": "直接人工", "diff_amount": 0.18}]},
            )
        return dashboard, benchmark

    dashboard, benchmark = with_temporary_store(lambda _: asyncio.run(generate_with_fallback()))
    assert dashboard["tasks"][0]["source"]["analysis_scenario"] == "成本看板归因分析"
    assert dashboard["tasks"][0]["source"]["attribution_conclusion"].startswith("直接材料成本")
    assert dashboard["tasks"][0]["analysis_evidence"]["alerts"][0]["metric"] == "直接材料"
    assert benchmark["tasks"][0]["source"]["analysis_scenario"] == "成本对标归因分析"
    assert benchmark["tasks"][0]["analysis_evidence"]["benchmark_differences"][0]["dimension"] == "直接人工"


def test_generation_does_not_persist_until_selected_drafts_are_saved():
    async def run():
        with patch.object(client, "_generate_llm_tasks", new_callable=AsyncMock, return_value=[
            {"task_title": "候选任务一"}, {"task_title": "候选任务二"},
        ]):
            generated = await client.generate_task_drafts(
                "银黄口服液", "2026-06", "dashboard_attribution", "材料成本异常", {},
            )
        assert client.TASK_STORE.list() == []
        saved = await client.save_selected_task_drafts(generated["tasks"][:1])
        assert saved["tasks_saved"] == 1
        assert len(client.TASK_STORE.list()) == 1

    with_temporary_store(lambda _: asyncio.run(run()))


def test_benchmark_suggestions_keep_distinct_task_titles():
    tasks = client._benchmark_suggestion_tasks(
        "银黄口服液", "2026-06", "成本对标归因分析", "单位成本存在差异", {
            "improvement_suggestions": [
                {"suggestion": "采购部共享金银花实际采购单价，建立联合采购机制", "department": "采购部"},
                {"suggestion": "生产部复核直接人工工时定额，优化排班效率", "department": "生产部"},
            ],
        },
    )
    assert len(tasks) == 2
    assert len({task["task_title"] for task in tasks}) == 2
    assert "联合采购" in tasks[0]["task_title"]


def test_benchmark_generation_calls_ai_before_structured_suggestion_fallback():
    async def run():
        with patch.object(client, "_generate_llm_tasks", new_callable=AsyncMock, return_value=[
            {"task_title": "复核原材料采购入库价与合同单价差异", "priority": "high"},
        ]) as generate:
            result = await client.generate_task_drafts(
                "银黄口服液", "2026-06", "benchmark_attribution", "对标差异结论", {
                    "improvement_suggestions": [{"suggestion": "结构化建议标题", "department": "采购部"}],
                },
            )
        assert generate.await_count == 1
        assert result["generation_source"] == "ai"
        assert result["tasks"][0]["task_title"] == "复核原材料采购入库价与合同单价差异"

    with_temporary_store(lambda _: asyncio.run(run()))


def test_vague_ai_tasks_are_rejected_and_replaced_with_executable_drafts():
    async def run():
        with patch.object(client, "_generate_llm_tasks", new_callable=AsyncMock, return_value=[
            {"task_title": "加强成本管理", "deadline": "2026-01-01"},
            {"task_title": "持续关注成本变化"},
        ]):
            return await client.generate_task_drafts(
                "银黄口服液", "2026-06", "dashboard_attribution", "直接材料采购价格异常", {},
            )

    result = with_temporary_store(lambda _: asyncio.run(run()))
    assert result["generation_source"] == "fallback"
    assert result["rejected_tasks"] == 2
    assert result["tasks"]
    assert all(client._is_executable_task(task) for task in result["tasks"])
    assert all(task["assignee"]["department"] and task["assignee"]["role"] for task in result["tasks"])


def test_only_selected_draft_is_dispatched_without_wechat_notification():
    async def fake_rpa(task):
        return {"code": 200, "data": {"task_id": task["task_id"], "status": "sent"}}

    first = client._make_task_draft("银黄口服液", "2026-06", {"task_title": "任务一"}, "成本对标分析", "材料成本异常")
    second = client._make_task_draft("银黄口服液", "2026-06", {"task_title": "任务二"}, "成本对标分析", "人工成本异常")
    def run_dispatch(_):
        client.TASK_STORE.upsert_many([first, second])
        with patch.object(client, "send_rpa_task", fake_rpa), patch.object(
            client, "send_wechat_notify", new_callable=AsyncMock, return_value={"status": "delivered"}
        ) as notify:
            result = asyncio.run(client.dispatch_selected_tasks([first["task_id"]]))
            assert notify.await_count == 0
            assert client.TASK_STORE.get(first["task_id"])["notification_status"] == "not_sent"
            return result

    result = with_temporary_store(run_dispatch)
    assert result["tasks_dispatched"] == 1
    assert result["results"][0]["status"] == "sent"


def test_selected_rpa_task_can_be_notified_later():
    first = client._make_task_draft("银黄口服液", "2026-06", {"task_title": "待通知任务"}, "成本对标分析", "材料成本异常")

    def run_notify(_):
        first["status"] = "sent"
        client.TASK_STORE.upsert(first)
        with patch.object(client, "send_wechat_notify", new_callable=AsyncMock, return_value={"status": "delivered"}) as notify:
            result = asyncio.run(client.notify_selected_tasks([first["task_id"]]))
            assert notify.await_count == 1
            return result

    result = with_temporary_store(run_notify)
    assert result["notifications_sent"] == 1


def test_stats_include_drafts_without_rpa_calls():
    task = client._make_task_draft("银黄口服液", "2026-06", {"task_title": "待派发任务"}, "成本对标分析", "待核查")
    def run_stats(_):
        client.TASK_STORE.upsert(task)
        with patch.object(client, "_refresh_task_statuses", AsyncMock()):
            return asyncio.run(client.get_rpa_stats())

    stats = with_temporary_store(run_stats)
    assert stats["generated"] == 1
    assert stats["draft"] == 1
    assert stats["dispatched"] == 0


def test_draft_persists_after_store_reopen_and_can_be_deleted():
    task = client._make_task_draft("银黄口服液", "2026-06", {"task_title": "持久化草稿"}, "成本看板归因分析", "材料成本异常")

    def run_persistence(db_path):
        client.TASK_STORE.upsert(task)
        client.TASK_STORE = TaskStore(db_path)
        restored = client.TASK_STORE.get(task["task_id"])
        assert restored and restored["task_title"] == "持久化草稿"
        updated = asyncio.run(client.update_rpa_task(task["task_id"], {
            "task_title": "已修改的持久化草稿",
            "assignee": {"name": "张伟", "department": "采购部", "role": "采购经理"},
            "priority": "high",
            "deadline": "2026-07-20",
        }))
        assert updated["task_title"] == "已修改的持久化草稿"
        assert updated["assignee"]["role"] == "采购经理"
        result = asyncio.run(client.delete_rpa_task(task["task_id"]))
        assert result["deleted"] is True
        assert client.TASK_STORE.get(task["task_id"]) is None

    with_temporary_store(run_persistence)


def test_sent_and_wechat_notified_task_can_be_deleted():
    task = client._make_task_draft("银黄口服液", "2026-06", {"task_title": "已通知任务"}, "成本看板归因分析", "材料成本异常")

    def run_delete(_):
        task["status"] = "completed"
        task["notification_status"] = "sent"
        client.TASK_STORE.upsert(task)
        result = asyncio.run(client.delete_rpa_task(task["task_id"]))
        assert result["deleted"] is True
        assert result["status"] == "completed"
        assert client.TASK_STORE.get(task["task_id"]) is None

    with_temporary_store(run_delete)


if __name__ == "__main__":
    cost_service.load_all()
    test_task_draft_has_required_business_fields()
    test_drafts_use_the_submitted_dashboard_and_benchmark_attribution_context()
    test_only_selected_draft_is_dispatched_without_wechat_notification()
    test_selected_rpa_task_can_be_notified_later()
    test_stats_include_drafts_without_rpa_calls()
    test_draft_persists_after_store_reopen_and_can_be_deleted()
    test_sent_and_wechat_notified_task_can_be_deleted()
    print("RPA tests passed")
