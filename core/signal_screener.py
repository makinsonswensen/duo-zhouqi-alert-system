# -*- coding: utf-8 -*-
"""
signal_screener.py - 多周期信号筛选器

三条并行筛选路径：
  阶梯确认：1h(形态+EMA7/25+过滤) → 4h(形态+EMA7) → 8h(形态+EMA7) 方向一致递进
  直接4h：形态 + 收盘在EMA7之上(多)/之下(空) + EMA7>EMA25(多)/EMA7<EMA25(空)
  直接8h：同直接4h条件，在8h周期上独立判定

注: 扫描发生在每小时 58 分，当前 1h K线尚未收盘。
"收盘"取当前价格（最后一根K线的最新 close），EMA 按含当前K线的序列计算。
"""
import logging
from core.pattern_detector import detect_patterns, compute_ema, compute_ema_slope
from core.signal_filters import apply_signal_filters

logger = logging.getLogger("screener")

DIR_UP = "up"      # 多头
DIR_DOWN = "down"  # 空头

DIR_LABEL = {DIR_UP: "多头(向上)", DIR_DOWN: "空头(向下)"}


def check_level(df, ema_periods, direction, filters=None, sig_filters=None):
    """
    检查单一周期是否满足: 形态 + 收盘在EMA上方/下方
    filters: 可选的过滤配置 dict（EMA排列、EMA斜率等）
    sig_filters: 可选的信号质量过滤配置（成交量/K线幅度），只用于信号入口级
    返回 dict {confirmed, patterns, ema_ok, close, ema_values, filters_detail}
    """
    if df is None or len(df) < 3:
        return {"confirmed": False, "patterns": [], "ema_ok": False,
                "close": None, "ema_values": {}, "filters_detail": {}}

    # 检测最新K线形态
    patterns = detect_patterns(df)
    # 只保留与目标方向一致的形态
    matched = [p for p in patterns if p["direction"] == direction]

    # 计算 EMA
    close_series = df["close"]
    ema_values = {}
    ema_series_cache = {}
    for p in ema_periods:
        s = compute_ema(close_series, p)
        ema_series_cache[p] = s
        ema_values[p] = float(s.iloc[-1])

    # 当前收盘价（最后一根K线的最新价格）
    close = float(df["close"].iloc[-1])

    # EMA 价格过滤（原有）
    if direction == DIR_UP:
        ema_ok = all(close > ema_values[p] for p in ema_periods)
    else:
        ema_ok = all(close < ema_values[p] for p in ema_periods)

    confirmed = ema_ok and len(matched) > 0

    filters_detail = {}

    # --- 信号质量过滤（成交量 / K线幅度），只加在信号入口级 ---
    if confirmed and sig_filters:
        sf_ok, sf_detail = apply_signal_filters(df, sig_filters)
        filters_detail.update(sf_detail)
        if not sf_ok:
            confirmed = False
            filters_detail["sig_filtered"] = True

    # --- 1h 过滤器 ---
    filters = filters or {}

    # ① EMA 排列过滤：多头 EMA7 > EMA25，空头 EMA7 < EMA25
    if confirmed and filters.get("ema_alignment") and len(ema_periods) >= 2:
        sorted_p = sorted(ema_periods)  # [7, 25]
        if direction == DIR_UP:
            alignment_ok = ema_values[sorted_p[0]] > ema_values[sorted_p[1]]
        else:
            alignment_ok = ema_values[sorted_p[0]] < ema_values[sorted_p[1]]
        filters_detail["ema_alignment"] = alignment_ok
        if not alignment_ok:
            confirmed = False

    # ② EMA 斜率过滤：EMA7 近 N 根斜率同向
    if confirmed and filters.get("ema_slope"):
        lookback = filters.get("ema_slope_lookback", 3)
        ema7_series = ema_series_cache[ema_periods[0]]
        slope = compute_ema_slope(ema7_series, lookback)
        if direction == DIR_UP:
            slope_ok = slope > 0
        else:
            slope_ok = slope < 0
        filters_detail["ema_slope"] = slope_ok
        filters_detail["ema_slope_value"] = round(slope, 6)
        if not slope_ok:
            confirmed = False

    return {
        "confirmed": confirmed,
        "patterns": matched,
        "ema_ok": ema_ok,
        "close": close,
        "ema_values": ema_values,
        "filters_detail": filters_detail,
    }


def check_direct_level(df, direction, sig_filters=None):
    """
    直接周期检测（不依赖前序周期确认）:
    - 形态识别（同方向）
    - 收盘在 EMA7 之上(多)/之下(空)
    - EMA7 > EMA25 (多) / EMA7 < EMA25 (空)
    - 信号质量过滤（成交量/K线幅度，可选）

    返回 dict {confirmed, patterns, close, ema_values, filters_detail}
    """
    if df is None or len(df) < 3:
        return {"confirmed": False, "patterns": [], "close": None, "ema_values": {},
                "filters_detail": {}}

    patterns = detect_patterns(df)
    matched = [p for p in patterns if p["direction"] == direction]
    if not matched:
        return {"confirmed": False, "patterns": [], "close": None, "ema_values": {},
                "filters_detail": {}}

    close_series = df["close"]
    ema7 = float(compute_ema(close_series, 7).iloc[-1])
    ema25 = float(compute_ema(close_series, 25).iloc[-1])
    close = float(df["close"].iloc[-1])

    if direction == DIR_UP:
        # 多头：收盘在EMA7之上 且 EMA7 > EMA25
        confirmed = close > ema7 and ema7 > ema25
    else:
        # 空头：收盘在EMA7之下 且 EMA7 < EMA25
        confirmed = close < ema7 and ema7 < ema25

    # --- 信号质量过滤（成交量 / K线幅度）---
    filters_detail = {}
    if confirmed and sig_filters:
        sf_ok, filters_detail = apply_signal_filters(df, sig_filters)
        if not sf_ok:
            confirmed = False
            filters_detail["sig_filtered"] = True

    return {
        "confirmed": confirmed,
        "patterns": matched,
        "close": close,
        "ema_values": {7: ema7, 25: ema25},
        "filters_detail": filters_detail,
    }


class SignalScreener:
    """多周期信号筛选器（阶梯确认 + 直接4h/8h筛选）"""

    def __init__(self, config, exchange_client, drop_last=False):
        self.config = config
        self.ex = exchange_client
        # 补扫模式：丢弃刚开盘的最后一根K线，改用刚收盘的那根
        self.drop_last = drop_last
        self.tf_level1 = config["timeframes"]["level1"]
        self.tf_level2 = config["timeframes"]["level2"]
        self.tf_level3 = config["timeframes"]["level3"]
        self.ema_l1 = config["timeframes"]["ema"]["level1"]
        self.ema_l2 = config["timeframes"]["ema"]["level2"]
        self.ema_l3 = config["timeframes"]["ema"]["level3"]
        self.ohlcv_limit = config["ohlcv"]["limit"]
        # 1h 过滤器配置
        self.filters = config.get("filters", {})
        # 信号质量过滤（成交量/K线幅度），作用于阶梯1h入口与直接4h/8h路径
        self.sig_filters = config.get("signal_filters", {})
        self.filtered_count = 0   # 本轮被质量过滤器拦截的信号数
        # 直接筛选配置
        ds = config.get("direct_screening", {})
        self.direct_4h_enabled = ds.get("enable_4h", True)
        self.direct_8h_enabled = ds.get("enable_8h", True)
        self.direct_4h_level = ds.get("alert_level_4h", 2)
        self.direct_8h_level = ds.get("alert_level_8h", 2)

    def _count_filtered(self, result):
        """统计被信号质量过滤器拦截的潜在信号"""
        if not result["confirmed"] and result.get("filters_detail", {}).get("sig_filtered"):
            self.filtered_count += 1

    def _fetch(self, symbol, timeframe):
        """拉取K线；补扫模式下丢弃刚开盘的最后一根，改用刚收盘的那根"""
        df = self.ex.fetch_ohlcv(symbol, timeframe, self.ohlcv_limit)
        return df.iloc[:-1] if self.drop_last else df

    def scan_symbol(self, symbol):
        """
        扫描单个交易对，返回信号列表 [(level, report), ...]
        一个交易对可能同时触发不同方向的信号（阶梯多头 + 直接4h空头）
        """
        signals = []

        # ---- 拉取 1h 数据 ----
        try:
            df1 = self._fetch(symbol, self.tf_level1)
        except Exception as e:
            logger.warning("⚠️ %s 获取 %s 失败: %s", self.ex.active_exchange, symbol, e)
            return signals

        # 4h/8h 数据延迟拉取（阶梯确认和直接筛选共享）
        df2 = None
        df3 = None

        # ===== 路径A：阶梯确认（1h → 4h → 8h）=====
        for direction in (DIR_UP, DIR_DOWN):
            r1 = check_level(df1, self.ema_l1, direction,
                             filters=self.filters, sig_filters=self.sig_filters)
            self._count_filtered(r1)
            if not r1["confirmed"]:
                continue

            # 1h 成立 → 拉 4h
            if df2 is None:
                try:
                    df2 = self._fetch(symbol, self.tf_level2)
                except Exception as e:
                    logger.warning("⚠️ %s 获取 %s %s 失败: %s", self.ex.active_exchange,
                                   symbol, self.tf_level2, e)
                    signals.append((1, self._build_ladder_report(symbol, direction, r1, None, None)))
                    continue

            r2 = check_level(df2, self.ema_l2, direction)
            if not r2["confirmed"]:
                signals.append((1, self._build_ladder_report(symbol, direction, r1, r2, None)))
                continue

            # 4h 成立 → 拉 8h
            if df3 is None:
                try:
                    df3 = self._fetch(symbol, self.tf_level3)
                except Exception as e:
                    logger.warning("⚠️ %s 获取 %s %s 失败: %s", self.ex.active_exchange,
                                   symbol, self.tf_level3, e)
                    signals.append((2, self._build_ladder_report(symbol, direction, r1, r2, None)))
                    continue

            r3 = check_level(df3, self.ema_l3, direction)
            if not r3["confirmed"]:
                signals.append((2, self._build_ladder_report(symbol, direction, r1, r2, r3)))
                continue

            # 三周期共振
            signals.append((3, self._build_ladder_report(symbol, direction, r1, r2, r3)))

        # ===== 路径B：直接4h筛选 =====
        if self.direct_4h_enabled:
            if df2 is None:
                try:
                    df2 = self._fetch(symbol, self.tf_level2)
                except Exception as e:
                    logger.warning("⚠️ 直接4h 获取 %s %s 失败: %s",
                                   symbol, self.tf_level2, e)
                    df2 = None
            if df2 is not None:
                for direction in (DIR_UP, DIR_DOWN):
                    r = check_direct_level(df2, direction, sig_filters=self.sig_filters)
                    self._count_filtered(r)
                    if r["confirmed"]:
                        signals.append((self.direct_4h_level,
                                        self._build_direct_report(symbol, direction, r, "直接4h")))

        # ===== 路径C：直接8h筛选 =====
        if self.direct_8h_enabled:
            if df3 is None:
                try:
                    df3 = self._fetch(symbol, self.tf_level3)
                except Exception as e:
                    logger.warning("⚠️ 直接8h 获取 %s %s 失败: %s",
                                   symbol, self.tf_level3, e)
                    df3 = None
            if df3 is not None:
                for direction in (DIR_UP, DIR_DOWN):
                    r = check_direct_level(df3, direction, sig_filters=self.sig_filters)
                    self._count_filtered(r)
                    if r["confirmed"]:
                        signals.append((self.direct_8h_level,
                                        self._build_direct_report(symbol, direction, r, "直接8h")))

        # ===== 去重：同一方向只保留最高级别信号 =====
        best = {}
        for level, report in signals:
            d = report["direction"]
            if d not in best or level > best[d][0]:
                best[d] = (level, report)

        return [(lvl, rep) for lvl, rep in best.values()]

    def _build_ladder_report(self, symbol, direction, r1, r2, r3):
        """构造阶梯确认信号详情报告"""
        report = {
            "symbol": symbol,
            "direction": direction,
            "direction_label": DIR_LABEL[direction],
            "source": "阶梯确认",
            "levels": {},
        }
        if r1:
            report["levels"]["1h"] = {
                "patterns": [p["pattern"] for p in r1["patterns"]],
                "close": r1["close"],
                "ema": r1["ema_values"],
                "filters": r1.get("filters_detail", {}),
            }
        if r2:
            report["levels"]["4h"] = {
                "patterns": [p["pattern"] for p in r2["patterns"]],
                "close": r2["close"],
                "ema": r2["ema_values"],
            }
        if r3:
            report["levels"]["8h"] = {
                "patterns": [p["pattern"] for p in r3["patterns"]],
                "close": r3["close"],
                "ema": r3["ema_values"],
            }
        return report

    def _build_direct_report(self, symbol, direction, result, source):
        """构造直接筛选信号详情报告"""
        tf_key = "4h" if "4h" in source else "8h"
        return {
            "symbol": symbol,
            "direction": direction,
            "direction_label": DIR_LABEL[direction],
            "source": source,
            "levels": {
                tf_key: {
                    "patterns": [p["pattern"] for p in result["patterns"]],
                    "close": result["close"],
                    "ema": result["ema_values"],
                }
            },
        }

    def scan_all(self, top_symbols):
        """扫描所有交易对，返回 [(symbol, level, report), ...]"""
        self.filtered_count = 0
        results = []
        for item in top_symbols:
            symbol = item["symbol"]
            for level, report in self.scan_symbol(symbol):
                results.append((symbol, level, report))
        if self.filtered_count:
            logger.info("🧹 信号质量过滤器拦截 %d 个低质量信号", self.filtered_count)
        return results
