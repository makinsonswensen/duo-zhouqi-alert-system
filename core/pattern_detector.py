# -*- coding: utf-8 -*-
"""
pattern_detector.py - 经典 K 线组合形态识别
只识别形态本身（不掺趋势判断），方向过滤交给 EMA 条件

多头形态（向上）:
    锤子线 Hammer、看涨吞没 Bullish Engulfing、早晨之星 Morning Star、
    刺透形态 Piercing Line、多头孕线 Bullish Harami
空头形态（向下）:
    射击之星 Shooting Star、看跌吞没 Bearish Engulfing、黄昏之星 Evening Star、
    乌云盖顶 Dark Cloud Cover、空头孕线 Bearish Harami
"""
import pandas as pd
import numpy as np

# 形态定义表
PATTERNS = {
    "hammer":             {"name": "锤子线",       "direction": "up",   "candles": 1},
    "bullish_engulfing":  {"name": "看涨吞没",     "direction": "up",   "candles": 2},
    "morning_star":       {"name": "早晨之星",     "direction": "up",   "candles": 3},
    "piercing_line":      {"name": "刺透形态",     "direction": "up",   "candles": 2},
    "bullish_harami":     {"name": "多头孕线",     "direction": "up",   "candles": 2},
    "shooting_star":      {"name": "射击之星",     "direction": "down", "candles": 1},
    "bearish_engulfing":  {"name": "看跌吞没",     "direction": "down", "candles": 2},
    "evening_star":       {"name": "黄昏之星",     "direction": "down", "candles": 3},
    "dark_cloud_cover":   {"name": "乌云盖顶",     "direction": "down", "candles": 2},
    "bearish_harami":     {"name": "空头孕线",     "direction": "down", "candles": 2},
}

BODY_RATIO = 0.3      # 实体占比阈值（相对整根K线幅度）
SHADOW_RATIO = 2.0    # 影线与实体长度比
DOJI_RATIO = 0.15     # 小实体（星线）相对幅度


def _body_size(o, c, h, l):
    """实体长度"""
    return abs(c - o)


def _range_size(o, c, h, l):
    """整根K线幅度（保护除零）"""
    return max(h - l, 1e-9)


def _upper_shadow(h, o, c):
    return h - max(o, c)


def _lower_shadow(l, o, c):
    return min(o, c) - l


def _is_hammer(c1):
    """锤子线：长下影（≥2×实体）、实体在上部、上影短、实体相对小"""
    o, c, h, l = c1["open"], c1["close"], c1["high"], c1["low"]
    rng = _range_size(o, c, h, l)
    body = _body_size(o, c, h, l)
    up_s = _upper_shadow(h, o, c)
    low_s = _lower_shadow(l, o, c)
    if body < 0.1 * rng:          # 实体太小不成形
        return False
    if body > 0.5 * rng:          # 实体过大不是锤子线
        return False
    if low_s < SHADOW_RATIO * body:  # 下影不够长
        return False
    if up_s > 0.1 * rng:          # 上影过长
        return False
    # 实体位于上部（实体底部在中线以上）
    if h - min(o, c) >= 0.5 * rng:
        return False
    return True


def _is_shooting_star(c1):
    """射击之星：长上影（≥2×实体）、实体在下部、下影短、实体相对小"""
    o, c, h, l = c1["open"], c1["close"], c1["high"], c1["low"]
    rng = _range_size(o, c, h, l)
    body = _body_size(o, c, h, l)
    up_s = _upper_shadow(h, o, c)
    low_s = _lower_shadow(l, o, c)
    if body < 0.1 * rng:
        return False
    if body > 0.5 * rng:
        return False
    if up_s < SHADOW_RATIO * body:  # 上影不够长
        return False
    if low_s > 0.1 * rng:          # 下影过长
        return False
    # 实体位于下部（实体顶部在中线以下）
    if max(o, c) - l >= 0.5 * rng:
        return False
    return True


def _is_engulfing(c1, c2, direction):
    """吞没形态：c2 实体完全包住 c1 实体"""
    if c2["close"] == c2["open"]:
        return False
    body1 = abs(c1["close"] - c1["open"])
    body2 = abs(c2["close"] - c2["open"])
    if body2 <= body1:
        return False
    if direction == "up":
        # c1 阴线，c2 阳线，c2 实体包住 c1 实体
        return (c1["close"] < c1["open"] and c2["close"] > c2["open"]
                and c2["open"] <= c1["close"] and c2["close"] >= c1["open"])
    else:
        # c1 阳线，c2 阴线
        return (c1["close"] > c1["open"] and c2["close"] < c2["open"]
                and c2["open"] >= c1["close"] and c2["close"] <= c1["open"])


def _is_harami(c1, c2, direction):
    """孕线：c2 小实体完全在 c1 实体内"""
    body1 = abs(c1["close"] - c1["open"])
    body2 = abs(c2["close"] - c2["open"])
    if body2 >= body1 * 0.8:
        return False
    hi1, lo1 = max(c1["open"], c1["close"]), min(c1["open"], c1["close"])
    hi2, lo2 = max(c2["open"], c2["close"]), min(c2["open"], c2["close"])
    if direction == "up":
        # c1 阴线，c2 阳线
        return (c1["close"] < c1["open"] and c2["close"] > c2["open"]
                and lo2 > lo1 and hi2 < hi1)
    else:
        # c1 阳线，c2 阴线
        return (c1["close"] > c1["open"] and c2["close"] < c2["open"]
                and lo2 > lo1 and hi2 < hi1)


def _is_star(c1, c2, c3, direction):
    """早晨之星/黄昏之星：阴/阳大实体 → 小实体星线 → 反向大实体"""
    # c1 必须是相应方向的大实体
    body1 = abs(c1["close"] - c1["open"])
    rng1 = _range_size(c1["open"], c1["close"], c1["high"], c1["low"])
    if body1 < 0.5 * rng1:
        return False
    # c2 是小实体（星线）
    body2 = abs(c2["close"] - c2["open"])
    rng2 = _range_size(c2["open"], c2["close"], c2["high"], c2["low"])
    if body2 > DOJI_RATIO * rng2:
        return False
    # c3 反向实体
    body3 = abs(c3["close"] - c3["open"])
    if body3 < 0.3 * _range_size(c3["open"], c3["close"], c3["high"], c3["low"]):
        return False

    if direction == "up":
        # c1 阴线，c3 阳线，c3 收盘站上 c1 实体中点
        if not (c1["close"] < c1["open"] and c3["close"] > c3["open"]):
            return False
        mid1 = (c1["open"] + c1["close"]) / 2
        return c3["close"] > mid1
    else:
        # c1 阳线，c3 阴线，c3 收盘跌破 c1 实体中点
        if not (c1["close"] > c1["open"] and c3["close"] < c3["open"]):
            return False
        mid1 = (c1["open"] + c1["close"]) / 2
        return c3["close"] < mid1


def _is_piercing(c1, c2):
    """刺透形态：阴线后阳线开盘破前低、收盘入前实体上半部"""
    if not (c1["close"] < c1["open"] and c2["close"] > c2["open"]):
        return False
    if c2["open"] >= c1["low"]:
        return False
    mid1 = (c1["open"] + c1["close"]) / 2
    return c2["close"] > mid1 and c2["close"] < c1["open"]


def _is_dark_cloud(c1, c2):
    """乌云盖顶：阳线后阴线开盘破前高、收盘入前实体下半部"""
    if not (c1["close"] > c1["open"] and c2["close"] < c2["open"]):
        return False
    if c2["open"] <= c1["high"]:
        return False
    mid1 = (c1["open"] + c1["close"]) / 2
    return c2["close"] < mid1 and c2["close"] > c1["open"]


def detect_patterns(df):
    """
    检测最新一根 K 线（含当前未收盘K线）是否有经典形态
    输入: DataFrame [timestamp, open, high, low, close, volume]
    输出: list of dict, 每个 {direction: 'up'|'down', pattern: 中文名, candles: 用了几根}
    """
    if len(df) < 3:
        return []

    # 取最后 3 根（当前形成中的K线 + 前两根已收盘）
    recent = df.tail(3).to_dict("records")
    c1, c2, c3 = recent[0], recent[1], recent[2]

    results = []

    # --- 1 根K线形态 ---
    if _is_hammer(c3):
        results.append({"direction": "up", "pattern": PATTERNS["hammer"]["name"], "candles": 1})
    if _is_shooting_star(c3):
        results.append({"direction": "down", "pattern": PATTERNS["shooting_star"]["name"], "candles": 1})

    # --- 2 根K线形态（c2,c3）---
    if _is_engulfing(c2, c3, "up"):
        results.append({"direction": "up", "pattern": PATTERNS["bullish_engulfing"]["name"], "candles": 2})
    if _is_engulfing(c2, c3, "down"):
        results.append({"direction": "down", "pattern": PATTERNS["bearish_engulfing"]["name"], "candles": 2})
    if _is_harami(c2, c3, "up"):
        results.append({"direction": "up", "pattern": PATTERNS["bullish_harami"]["name"], "candles": 2})
    if _is_harami(c2, c3, "down"):
        results.append({"direction": "down", "pattern": PATTERNS["bearish_harami"]["name"], "candles": 2})
    if _is_piercing(c2, c3):
        results.append({"direction": "up", "pattern": PATTERNS["piercing_line"]["name"], "candles": 2})
    if _is_dark_cloud(c2, c3):
        results.append({"direction": "down", "pattern": PATTERNS["dark_cloud_cover"]["name"], "candles": 2})

    # --- 3 根K线形态（c1,c2,c3）---
    if _is_star(c1, c2, c3, "up"):
        results.append({"direction": "up", "pattern": PATTERNS["morning_star"]["name"], "candles": 3})
    if _is_star(c1, c2, c3, "down"):
        results.append({"direction": "down", "pattern": PATTERNS["evening_star"]["name"], "candles": 3})

    return results


def compute_ema(series, period):
    """计算 EMA，返回与输入等长的 Series"""
    return series.ewm(span=period, adjust=False).mean()


def compute_ema_slope(ema_series, lookback=3):
    """
    计算 EMA 近 N 根的斜率方向
    正值 = 上升，负值 = 下降
    用首尾差值，简单稳健
    """
    vals = ema_series.tail(lookback).values
    if len(vals) < 2:
        return 0.0
    return float(vals[-1] - vals[0])
