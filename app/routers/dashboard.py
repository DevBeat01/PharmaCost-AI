"""看板API路由"""
import json
import logging
from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool
from security import validate_product, validate_month

router = APIRouter()
logger = logging.getLogger("routers.dashboard")


@router.get("/three-dim")
async def get_three_dim(product: str = Query(...), month: str = Query(...)):
    """三维对比数据"""
    product = validate_product(product)
    month = validate_month(month)
    from analysis.dashboard import three_dim_compare
    return three_dim_compare(product, month)


@router.get("/trend")
async def get_trend(product: str = Query(...)):
    """近6个月趋势数据"""
    product = validate_product(product)
    from analysis.dashboard import cost_trend
    return cost_trend(product)


@router.get("/heatmap")
async def get_heatmap():
    """产品×月份×成本要素热力图数据"""
    from analysis.dashboard import cost_heatmap
    return cost_heatmap()


@router.get("/structure")
async def get_structure(product: str = Query(...), month: str = Query(...)):
    """成本结构饼图"""
    product = validate_product(product)
    month = validate_month(month)
    from analysis.dashboard import cost_structure
    return cost_structure(product, month)


@router.get("/attribution")
async def get_attribution(product: str = Query(...), month: str = Query(...), force: bool = Query(False)):
    """基于看板JSON生成成本归因分析"""
    product = validate_product(product)
    month = validate_month(month)
    from analysis.dashboard import dashboard_attribution
    # 模型调用是同步阻塞操作，放入线程池避免阻塞其他看板接口。
    return await run_in_threadpool(dashboard_attribution, product, month, force)


@router.get("/attribution/stream")
async def get_attribution_stream(product: str = Query(...), month: str = Query(...), force: bool = Query(False)):
    """以 SSE 流式返回看板归因文本。"""
    product = validate_product(product)
    month = validate_month(month)
    logger.info("归因分析SSE请求: product=%s month=%s force=%s", product, month, force)
    from analysis.dashboard import dashboard_attribution_stream

    def events():
        for item in dashboard_attribution_stream(product, month, force):
            yield f"event: {item.get('event', 'message')}\ndata: {json.dumps(item.get('data', {}), ensure_ascii=False)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    })


@router.get("/waterfall")
async def get_waterfall(product: str = Query(...), month: str = Query(...)):
    """成本变动瀑布图"""
    product = validate_product(product)
    month = validate_month(month)
    from analysis.dashboard import cost_waterfall
    return cost_waterfall(product, month)


@router.get("/material-detail")
async def get_material_detail(product: str = Query(...), month: str = Query(...)):
    """原材料明细"""
    product = validate_product(product)
    month = validate_month(month)
    from analysis.dashboard import material_detail_table
    return material_detail_table(product, month)


@router.get("/overhead-detail")
async def get_overhead_detail(product: str = Query(...), month: str = Query(...)):
    """制造费用明细"""
    product = validate_product(product)
    month = validate_month(month)
    from analysis.dashboard import overhead_detail_table
    return overhead_detail_table(product, month)


@router.get("/labor-metrics")
async def get_labor_metrics(product: str = Query(...), month: str = Query(...)):
    """人工工时指标"""
    product = validate_product(product)
    month = validate_month(month)
    from analysis.dashboard import labor_metrics
    return labor_metrics(product, month)


@router.get("/industry-bench")
async def get_industry_bench(product: str = Query(...)):
    """行业基准"""
    product = validate_product(product)
    from analysis.dashboard import industry_benchmark
    return industry_benchmark(product)


@router.get("/forecast")
async def get_forecast(product: str = Query(...), method: str = Query("moving_average")):
    """成本预测（加分项）"""
    product = validate_product(product)
    if method not in ("moving_average", "linear"):
        method = "moving_average"
    from analysis.forecast import moving_average_forecast, linear_regression_forecast
    if method == "linear":
        return linear_regression_forecast(product)
    return moving_average_forecast(product)
