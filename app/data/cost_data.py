"""CSV数据统一加载与查询服务"""
import pandas as pd
from pathlib import Path
from typing import Optional
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import PRODUCT_SPECS, get_data_file


class CostDataService:
    """统一的成本数据查询服务"""

    def __init__(self):
        self._loaded = False
        self.cost_2026: pd.DataFrame = pd.DataFrame()
        self.cost_2025: pd.DataFrame = pd.DataFrame()
        self.material: pd.DataFrame = pd.DataFrame()
        self.overhead: pd.DataFrame = pd.DataFrame()
        self.budget: pd.DataFrame = pd.DataFrame()
        self.labor: pd.DataFrame = pd.DataFrame()
        self.bench_2026: pd.DataFrame = pd.DataFrame()
        self.bench_2025: pd.DataFrame = pd.DataFrame()
        self.market: pd.DataFrame = pd.DataFrame()
        self.industry: pd.DataFrame = pd.DataFrame()
        self.products: list[str] = []
        self.months: list[str] = []

    def load_all(self):
        """加载所有CSV数据"""
        if not get_data_file("cost_2026").is_file():
            from config import DATA_DIR
            raise RuntimeError(
                "未找到竞赛数据包。请将数据包放置在项目根目录的"
                "'创灵境_考题模拟数据/'，或设置 DATA_DIR 指向该目录。"
                f" 当前 DATA_DIR: {DATA_DIR}"
            )
        self.cost_2026 = self._load(get_data_file("cost_2026"))
        self.cost_2025 = self._load(get_data_file("cost_2025"))
        self.material = self._load(get_data_file("material"))
        self.overhead = self._load(get_data_file("overhead"))
        self.budget = self._load(get_data_file("budget"))
        self.labor = self._load(get_data_file("labor"))
        self.bench_2026 = self._load(get_data_file("benchmark_2026"))
        self.bench_2025 = self._load(get_data_file("benchmark_2025"))
        self.market = self._load(get_data_file("market"))
        self.industry = self._load(get_data_file("industry"))
        self._refresh_dimensions()
        self._loaded = True
        print(f"数据加载完成: 10个CSV文件已就绪")

    def _load(self, path: Path) -> pd.DataFrame:
        """加载单个CSV，自动处理BOM"""
        df = pd.read_csv(path, encoding='utf-8-sig')
        df.columns = df.columns.str.strip()
        return df

    def _check_loaded(self):
        if not self._loaded:
            raise RuntimeError("数据尚未加载，请先调用 load_all()")

    def _refresh_dimensions(self) -> None:
        """Derive selector dimensions from the currently loaded data."""
        # The current-year cost summary is the authoritative source for the
        # selectors: detail/benchmark files can legitimately contain stale or
        # supplemental rows that do not have a corresponding dashboard row.
        products: set[str] = set()
        if '产品名称' in self.cost_2026.columns:
            products.update(self.cost_2026['产品名称'].dropna().astype(str).str.strip())
        if not products:
            # Keep the service usable for partially provisioned data bundles.
            frames = [self.cost_2025, self.material, self.overhead, self.budget,
                      self.labor, self.bench_2026, self.bench_2025]
            for frame in frames:
                if '产品名称' in frame.columns:
                    products.update(frame['产品名称'].dropna().astype(str).str.strip())
        self.products = sorted(p for p in products if p)
        values = []
        if '月份' in self.cost_2026.columns:
            values = self.cost_2026['月份'].dropna().astype(str).str.strip().unique().tolist()
        self.months = sorted((m for m in values if m), key=self._month_sort_key)

    @staticmethod
    def _month_sort_key(month: str) -> tuple[int, int, str]:
        try:
            year, number = str(month).split('-', 1)
            return int(year), int(number), str(month)
        except (ValueError, AttributeError):
            return 0, 0, str(month)

    def _previous_month(self, month: str) -> str | None:
        if month not in self.months:
            return None
        index = self.months.index(month)
        return self.months[index - 1] if index > 0 else None

    # ==================== 产品列表 ====================

    def get_products(self) -> list[dict]:
        """获取产品列表"""
        return [{"name": p, "spec": PRODUCT_SPECS.get(p, "")} for p in self.products]

    def get_months(self) -> list[str]:
        """获取可用月份"""
        return list(self.months)

    # ==================== 成本汇总查询 ====================

    def get_cost_summary(self, product: str, month: str, factory: str = "中药一厂") -> Optional[dict]:
        """获取指定产品+月份的成本汇总"""
        self._check_loaded()
        df = self.cost_2026
        row = df[(df['产品名称'] == product) & (df['月份'] == month) & (df['工厂'] == factory)]
        if row.empty:
            return None
        r = row.iloc[0]
        return {
            'factory': r['工厂'], 'product': r['产品名称'], 'spec': r['产品规格'],
            'month': r['月份'], 'production': int(r['产量(盒)']),
            'material_cost': float(r['直接材料(元/盒)']),
            'labor_cost': float(r['直接人工(元/盒)']),
            'overhead_cost': float(r['制造费用(元/盒)']),
            'unit_cost': float(r['单位成本(元/盒)']),
            'total_cost': float(r['总成本(元)']),
        }

    def get_cost_summary_prev_month(self, product: str, month: str) -> Optional[dict]:
        """获取上月成本汇总"""
        previous_month = self._previous_month(month)
        if not previous_month:
            return None
        return self.get_cost_summary(product, previous_month)

    def get_cost_summary_last_year(self, product: str, month: str) -> Optional[dict]:
        """获取去年同月成本汇总"""
        self._check_loaded()
        try:
            year, month_number = str(month).split('-', 1)
            last_year_month = f"{int(year) - 1:04d}-{month_number}"
        except (ValueError, TypeError):
            return None
        row = self.cost_2025[
            (self.cost_2025['产品名称'] == product) &
            (self.cost_2025['月份'] == last_year_month) &
            (self.cost_2025['工厂'] == "中药一厂")
        ]
        if row.empty:
            return None
        r = row.iloc[0]
        return {
            'factory': r['工厂'], 'product': r['产品名称'],
            'month': r['月份'], 'production': int(r['产量(盒)']),
            'material_cost': float(r['直接材料(元/盒)']),
            'labor_cost': float(r['直接人工(元/盒)']),
            'overhead_cost': float(r['制造费用(元/盒)']),
            'unit_cost': float(r['单位成本(元/盒)']),
            'total_cost': float(r['总成本(元)']),
        }

    # ==================== 趋势数据 ====================

    def get_cost_trend(self, product: str, factory: str = "中药一厂") -> list[dict]:
        """获取近6个月成本趋势"""
        self._check_loaded()
        df = self.cost_2026
        rows = df[(df['产品名称'] == product) & (df['工厂'] == factory)].sort_values('月份')
        result = []
        for _, r in rows.iterrows():
            result.append({
                'month': r['月份'],
                'production': int(r['产量(盒)']),
                'material': float(r['直接材料(元/盒)']),
                'labor': float(r['直接人工(元/盒)']),
                'overhead': float(r['制造费用(元/盒)']),
                'unit_cost': float(r['单位成本(元/盒)']),
            })
        return result

    # ==================== 原材料明细 ====================

    def get_material_detail(self, product: str, month: str) -> list[dict]:
        """获取原材料消耗明细"""
        self._check_loaded()
        rows = self.material[
            (self.material['产品名称'] == product) & (self.material['月份'] == month)
        ].sort_values('单位消耗成本(元/盒)', ascending=False)
        result = []
        for _, r in rows.iterrows():
            ratio_str = str(r['占总材料成本比例']).replace('%', '')
            result.append({
                'material_name': r['原材料名称'],
                'unit_cost': float(r['单位消耗成本(元/盒)']),
                'total_cost': float(r['原材料总成本(元)']),
                'ratio': float(ratio_str),
            })
        return result

    def get_material_detail_prev(self, product: str, month: str) -> dict[str, float]:
        """获取上月原材料单位成本（用于环比计算）"""
        previous_month = self._previous_month(month)
        if not previous_month:
            return {}
        prev = self.get_material_detail(product, previous_month)
        return {m['material_name']: m['unit_cost'] for m in prev}

    # ==================== 制造费用明细 ====================

    def get_overhead_detail(self, product: str, month: str) -> list[dict]:
        """获取制造费用明细"""
        self._check_loaded()
        rows = self.overhead[
            (self.overhead['产品名称'] == product) & (self.overhead['月份'] == month)
        ]
        result = []
        for _, r in rows.iterrows():
            result.append({
                'category': r['费用类别'],
                'unit_cost': float(r['单位费用(元/盒)']),
                'total_cost': float(r['费用总额(元)']),
            })
        return result

    def get_overhead_detail_prev(self, product: str, month: str) -> dict[str, float]:
        """获取上月制造费用（用于环比计算）"""
        previous_month = self._previous_month(month)
        if not previous_month:
            return {}
        prev = self.get_overhead_detail(product, previous_month)
        return {o['category']: o['unit_cost'] for o in prev}

    # ==================== 人工工时明细 ====================

    def get_labor_detail(self, product: str, month: str) -> Optional[dict]:
        """获取人工工时明细"""
        self._check_loaded()
        row = self.labor[
            (self.labor['产品名称'] == product) & (self.labor['月份'] == month)
        ]
        if row.empty:
            return None
        r = row.iloc[0]
        return {
            'production': int(r['产量(盒)']),
            'total_labor_cost': float(r['直接人工总额(元)']),
            'total_hours': float(r['总工时(小时)']),
            'worker_count': int(r['生产人数(人)']),
            'work_days': int(r['工作天数(天)']),
        }

    def get_labor_detail_prev(self, product: str, month: str) -> Optional[dict]:
        """获取上月人工工时"""
        previous_month = self._previous_month(month)
        if not previous_month:
            return None
        return self.get_labor_detail(product, previous_month)

    # ==================== 预算数据 ====================

    def get_budget(self, product: str, month: str) -> Optional[dict]:
        """获取预算数据"""
        self._check_loaded()
        row = self.budget[
            (self.budget['产品名称'] == product) & (self.budget['月份'] == month)
        ]
        if row.empty:
            return None
        r = row.iloc[0]
        return {
            'budget_production': int(r['预算产量(盒)']),
            'budget_material': float(r['预算直接材料(元/盒)']),
            'budget_labor': float(r['预算直接人工(元/盒)']),
            'budget_overhead': float(r['预算制造费用(元/盒)']),
            'budget_unit_cost': float(r['预算单位成本(元/盒)']),
            'budget_total_cost': float(r['预算总成本(元)']),
        }

    # ==================== 对标数据（中药二厂） ====================

    def get_benchmark(self, product: str, month: str) -> Optional[dict]:
        """获取对标工厂（二厂）成本数据"""
        self._check_loaded()
        row = self.bench_2026[
            (self.bench_2026['产品名称'] == product) &
            (self.bench_2026['月份'] == month)
        ]
        if row.empty:
            return None
        r = row.iloc[0]
        return {
            'factory': r['工厂'], 'product': r['产品名称'],
            'month': r['月份'], 'production': int(r['产量(盒)']),
            'material_cost': float(r['直接材料(元/盒)']),
            'labor_cost': float(r['直接人工(元/盒)']),
            'overhead_cost': float(r['制造费用(元/盒)']),
            'unit_cost': float(r['单位成本(元/盒)']),
            'total_cost': float(r['总成本(元)']),
        }

    # ==================== 市场行情 ====================

    def get_market_prices(self, month: Optional[str] = None) -> list[dict]:
        """获取药材市场价格行情"""
        self._check_loaded()
        result = []
        month_num = int(str(month).split('-')[1]) if month and '-' in str(month) else None
        for _, r in self.market.iterrows():
            prices = {}
            for m in range(1, 13):
                col = f"{m}月价格"
                if col in r.index and pd.notna(r[col]):
                    try:
                        prices[f"2026-{m:02d}"] = float(r[col])
                    except (TypeError, ValueError):
                        continue
            price_col = f"{month_num}月价格" if month_num else ""
            current_price = prices.get(f"2026-{month_num:02d}") if month_num else next(iter(prices.values()), 0)
            result.append({
                'material_name': str(r.get('药材名称', '')),
                'spec': str(r.get('规格等级', '')),
                'unit': str(r.get('单位', '')),
                'current_price': current_price or 0,
                'prices': prices,
                'source': str(r.get('价格来源', '')),
                'trend': str(r.get('趋势分析', '')),
            })
        return result

    def get_market_price_for_material(self, material_name: str, month: Optional[str] = None) -> Optional[dict]:
        """获取特定药材的市场行情"""
        prices = self.get_market_prices(month)
        for p in prices:
            if material_name in p['material_name'] or p['material_name'] in material_name:
                return p
        return None

    # ==================== 行业基准 ====================

    def get_industry_benchmarks(self, category: Optional[str] = None) -> list[dict]:
        """获取行业基准数据"""
        self._check_loaded()
        df = self.industry
        if category:
            df = df[df['产品类别'] == category]
        result = []
        for _, r in df.iterrows():
            result.append({
                'category': r['产品类别'],
                'metric': r['指标'],
                'p25': str(r['行业P25']),
                'p50': str(r['行业P50']),
                'p75': str(r['行业P75']),
                'factory_value': str(r['本厂水平(中药一厂)']),
                'evaluation': str(r['对标评价']),
            })
        return result

    # ==================== 辅助计算 ====================

    def calc_mom_change(self, current: float, previous: float) -> float:
        """计算环比变动率(%)"""
        if previous == 0:
            return 0.0
        return round((current - previous) / previous * 100, 2)

    def calc_budget_deviation(self, actual: float, budget: float) -> float:
        """计算预算偏差率(%)"""
        if budget == 0:
            return 0.0
        return round((actual - budget) / budget * 100, 2)


# 全局单例
cost_service = CostDataService()
