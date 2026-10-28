# -*- coding: utf-8 -*-
"""
signal_filters.py - 信号质量过滤器
作用于信号入口（阶梯1h / 直接4h / 直接8h），过滤低质量形态信号：

  ① 成交量确认：形态当根K线成交量 ≥ 前 N 根均量 × 倍数
     （放量反转形态的可靠性显著高于缩量形态）
  ② K线幅度过滤：形态当根振幅 ≥ ATR(14) × 倍数
     （过滤振幅过小、没有实际意义的迷你K线）

注：扫描发生在当根 K 线未收盘时，成交量/振幅只会随时间增长，
    因此未收盘 K 线通过过滤 ⇒ 收盘后也必然通过，不会"收盘后失效"。
    均量/ATR 均按"不含当根"的前 N 根计算，避免当根自我抬高门槛。
"""
import pandas as pd


def volume_confirmed(df, ma_period=20, mult=1.5):
    """
    成交量确认：最后一根K线成交量 ≥ 前 ma_period 根均量 × mult
    返回 (是否通过, 详情dict)
    """
    vol = df["volume"]
    hist = vol.iloc[-(ma_period + 1):-1]  # 不含当根的前 N 根
    if len(hist) < 5:
        return True, {"reason": "历史不足，跳过成交量过滤"}
    cur = float(vol.iloc[-1])
    ma = float(hist.mean())
    ok = (cur >= mult * ma) if ma > 0 else True
    return ok, {"volume": round(cur, 2), "vol_ma": round(ma, 2), "mult": mult}


def range_confirmed(df, atr_period=14, mult=0.5):
    """
    K线幅度过滤：最后一根振幅 ≥ ATR(atr_period) × mult
    ATR 取 TR（真实波幅）的简单平均，按不含当根的前 N 根计算
    返回 (是否通过, 详情dict)
    """
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([high - low,
                    (high - prev_close).abs(),
                    (low - prev_close).abs()], axis=1).max(axis=1)
    hist_tr = tr.iloc[-(atr_period + 1):-1].dropna()  # 不含当根
    if len(hist_tr) < 5:
        return True, {"reason": "历史不足，跳过幅度过滤"}
    atr = float(hist_tr.mean())
    rng = float(high.iloc[-1] - low.iloc[-1])
    ok = (rng >= mult * atr) if atr > 0 else True
    return ok, {"range": round(rng, 6), "atr": round(atr, 6), "mult": mult}


def apply_signal_filters(df, cfg):
    """
    按配置依次应用过滤器（任一不通过即拦截）。
    cfg: config["signal_filters"] 子树
    返回 (是否全部通过, 详情dict)
    """
    detail = {}

    vol_cfg = (cfg or {}).get("volume", {})
    if vol_cfg.get("enabled"):
        ok, d = volume_confirmed(df, ma_period=vol_cfg.get("ma_period", 20),
                                 mult=vol_cfg.get("mult", 1.5))
        detail["volume"] = d
        if not ok:
            return False, detail

    rng_cfg = (cfg or {}).get("range", {})
    if rng_cfg.get("enabled"):
        ok, d = range_confirmed(df, atr_period=rng_cfg.get("atr_period", 14),
                                mult=rng_cfg.get("mult", 0.5))
        detail["range"] = d
        if not ok:
            return False, detail

    return True, detail
