"""回归测试：导入不同月份长度后，选择器及参数校验跟随活动数据。"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))

from data.cost_data import CostDataService, cost_service
from security import validate_month, validate_product


def test_dimensions_are_derived_from_current_year_cost_rows():
    service = CostDataService()
    service.cost_2026 = pd.DataFrame({
        "产品名称": ["新产品2", "新产品2", "另一产品"],
        "月份": ["2026-08", "2026-10", "2026-09"],
    })
    service._refresh_dimensions()

    assert service.get_products() == [{"name": "另一产品", "spec": ""}, {"name": "新产品2", "spec": ""}]
    assert service.get_months() == ["2026-08", "2026-09", "2026-10"]


def test_security_validation_accepts_active_import_dimensions(monkeypatch):
    monkeypatch.setattr(cost_service, "products", ["新产品2"])
    monkeypatch.setattr(cost_service, "months", ["2026-08"])

    assert validate_product("新产品2") == "新产品2"
    assert validate_month("2026-08") == "2026-08"
    with pytest.raises(Exception):
        validate_month("2026-06")
