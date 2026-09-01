"""成本预测模块 — 移动平均 + 简单线性回归（加分项）"""
import sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.cost_data import cost_service


def moving_average_forecast(product: str, periods: int = 3) -> dict:
    """移动平均预测下月成本"""
    trend = cost_service.get_cost_trend(product)
    if len(trend) < 3:
        return {"error": "数据不足，至少需要3个月数据"}

    metrics = ['material', 'labor', 'overhead', 'unit_cost']
    forecasts = {}

    for metric in metrics:
        values = [t[metric] for t in trend]
        ma = np.mean(values[-periods:])
        std = np.std(values[-periods:])
        forecasts[metric] = {
            'forecast': round(float(ma), 2),
            'confidence_low': round(float(ma - 1.96 * std), 2),
            'confidence_high': round(float(ma + 1.96 * std), 2),
        }

    # 预测产量
    productions = [t['production'] for t in trend]
    prod_ma = np.mean(productions[-periods:])
    prod_std = np.std(productions[-periods:])

    last_month = trend[-1]['month']
    next_month_num = int(last_month.split('-')[1]) + 1
    next_year = int(last_month.split('-')[0])
    if next_month_num > 12:
        next_month_num = 1
        next_year += 1
    next_month = f"{next_year}-{next_month_num:02d}"

    return {
        'product': product,
        'last_month': last_month,
        'next_month': next_month,
        'method': 'moving_average',
        'periods': periods,
        'forecasts': forecasts,
        'production_forecast': {
            'forecast': int(prod_ma),
            'confidence_low': int(max(0, prod_ma - 1.96 * prod_std)),
            'confidence_high': int(prod_ma + 1.96 * prod_std),
        },
        'history': trend,
    }


def linear_regression_forecast(product: str) -> dict:
    """简单线性回归预测下月成本"""
    trend = cost_service.get_cost_trend(product)
    if len(trend) < 3:
        return {"error": "数据不足"}

    x = np.arange(len(trend))
    metrics = ['material', 'labor', 'overhead', 'unit_cost']
    forecasts = {}

    for metric in metrics:
        y = np.array([t[metric] for t in trend])
        # 简单线性回归
        coeffs = np.polyfit(x, y, 1)
        slope, intercept = coeffs
        next_x = len(trend)
        pred = slope * next_x + intercept

        # 置信区间（基于残差标准差）
        residuals = y - (slope * x + intercept)
        std = np.std(residuals)

        forecasts[metric] = {
            'forecast': round(float(pred), 2),
            'confidence_low': round(float(pred - 1.96 * std), 2),
            'confidence_high': round(float(pred + 1.96 * std), 2),
            'trend': '上升' if slope > 0.01 else ('下降' if slope < -0.01 else '平稳'),
            'slope': round(float(slope), 4),
        }

    # 预测产量
    y_prod = np.array([t['production'] for t in trend])
    coeffs_prod = np.polyfit(x, y_prod, 1)
    prod_pred = coeffs_prod[0] * len(trend) + coeffs_prod[1]
    prod_residuals = y_prod - (coeffs_prod[0] * x + coeffs_prod[1])
    prod_std = np.std(prod_residuals)

    last_month = trend[-1]['month']
    next_month_num = int(last_month.split('-')[1]) + 1
    next_year = int(last_month.split('-')[0])
    if next_month_num > 12:
        next_month_num = 1
        next_year += 1
    next_month = f"{next_year}-{next_month_num:02d}"

    return {
        'product': product,
        'last_month': last_month,
        'next_month': next_month,
        'method': 'linear_regression',
        'forecasts': forecasts,
        'production_forecast': {
            'forecast': int(max(0, prod_pred)),
            'confidence_low': int(max(0, prod_pred - 1.96 * prod_std)),
            'confidence_high': int(prod_pred + 1.96 * prod_std),
            'trend': '上升' if coeffs_prod[0] > 0 else '下降',
        },
        'history': trend,
    }
