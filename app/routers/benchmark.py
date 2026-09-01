"""对标分析API路由"""
from fastapi import APIRouter, Query
from security import validate_product, validate_month

router = APIRouter()


@router.get("/diff")
async def get_diff(product: str = Query(...), month: str = Query(...)):
    """对标差异总览"""
    product = validate_product(product)
    month = validate_month(month)
    from analysis.benchmark import benchmark_diff
    return benchmark_diff(product, month)


@router.get("/breakdown")
async def get_breakdown(product: str = Query(...), month: str = Query(...)):
    """对标结构拆解"""
    product = validate_product(product)
    month = validate_month(month)
    from analysis.benchmark import benchmark_breakdown
    return benchmark_breakdown(product, month)


@router.get("/structure")
async def get_structure(product: str = Query(...), month: str = Query(...)):
    """对标差异结构树（材料节点可下钻至原材料明细）"""
    product = validate_product(product)
    month = validate_month(month)
    from analysis.benchmark import benchmark_structure_tree
    return benchmark_structure_tree(product, month)


@router.get("/attribution")
async def get_attribution(
    product: str = Query(...), month: str = Query(...), force: bool = Query(False)
):
    """对标归因分析（LLM生成）"""
    product = validate_product(product)
    month = validate_month(month)
    from analysis.benchmark import benchmark_attribution
    return await benchmark_attribution(product, month, force=force)
