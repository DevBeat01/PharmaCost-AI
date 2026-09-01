"""Pydantic数据模型定义"""
from pydantic import BaseModel
from typing import Optional


class ProductInfo(BaseModel):
    name: str
    spec: str


class CostSummary(BaseModel):
    factory: str
    product: str
    spec: str
    month: str
    production: int
    material_cost: float
    labor_cost: float
    overhead_cost: float
    unit_cost: float
    total_cost: float


class MaterialDetail(BaseModel):
    factory: str
    product: str
    spec: str
    month: str
    production: int
    material_name: str
    unit_cost: float
    total_cost: float
    ratio: float


class OverheadDetail(BaseModel):
    factory: str
    product: str
    spec: str
    month: str
    production: int
    category: str
    unit_cost: float
    total_cost: float


class LaborDetail(BaseModel):
    factory: str
    product: str
    spec: str
    month: str
    production: int
    total_labor_cost: float
    total_hours: float
    worker_count: int
    work_days: int


class BudgetData(BaseModel):
    factory: str
    product: str
    spec: str
    month: str
    budget_production: int
    budget_material: float
    budget_labor: float
    budget_overhead: float
    budget_unit_cost: float
    budget_total_cost: float


class MarketPrice(BaseModel):
    material_name: str
    spec_grade: str
    unit: str
    prices: dict[str, float]  # month -> price
    source: str
    trend: str


class IndustryBench(BaseModel):
    category: str
    metric: str
    p25: str
    p50: str
    p75: str
    factory_value: str
    evaluation: str


class ThreeDimRow(BaseModel):
    metric: str
    current: float
    last_month: float
    mom_change: float
    last_year: float
    yoy_change: float
    budget: float
    budget_deviation: float


class TrendPoint(BaseModel):
    month: str
    material: float
    labor: float
    overhead: float
    unit_cost: float


class WaterfallItem(BaseModel):
    name: str
    value: float


class BenchmarkDiff(BaseModel):
    dimension: str
    factory1: float
    factory2: float
    diff_amount: float
    diff_rate: float
    direction: str


class RPATask(BaseModel):
    task_id: str
    task_title: str
    assignee_name: str
    assignee_department: str
    assignee_role: str
    analysis_type: str
    analysis_month: str
    product: str
    finding: str
    priority: str
    deadline: str
    suggestion: str
    notify_method: str = "wechat"
    created_at: str
