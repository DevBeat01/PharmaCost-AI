"""Second-half market-price imports must remain valid and queryable."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))


def test_market_data_accepts_second_half_columns_and_merges_periods():
    from routers.settings import _merge_market_data, _validate_data_columns

    first_half = pd.DataFrame({
        "药材名称": ["黄芩"], "规格等级": ["统货"], "单位": ["元/kg"],
        "1月价格": [10.0], "价格来源": ["市场甲"],
    })
    second_half = pd.DataFrame({
        "药材名称": ["黄芩"], "规格等级": ["统货"], "单位": ["元/kg"],
        "7月价格": [13.0], "趋势分析": ["上涨"],
    })

    _validate_data_columns("market", set(second_half.columns), set(first_half.columns))
    merged = _merge_market_data(first_half, second_half)
    assert len(merged) == 1
    assert merged.loc[0, "1月价格"] == 10.0
    assert merged.loc[0, "7月价格"] == 13.0


def test_market_price_reader_supports_months_seven_to_twelve():
    from data.cost_data import CostDataService

    service = CostDataService()
    service._loaded = True
    service.market = pd.DataFrame({
        "药材名称": ["黄芩"], "7月价格": [13.0], "12月价格": [15.0],
    })

    july = service.get_market_prices("2026-07")[0]
    december = service.get_market_price_for_material("黄芩", "2026-12")
    assert july["current_price"] == 13.0
    assert december and december["current_price"] == 15.0
