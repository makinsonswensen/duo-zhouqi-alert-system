# -*- coding: utf-8 -*-
"""
main.py - 多周期确认系统提醒 主控入口

每根 1h K 线第 58 分钟自动触发扫描:
  1. 获取成交量前 50 的 USDT 永续交易对（Binance → Gate.io → OKX 自动切换）
  2. 逐对扫描 1h → 4h → 8h 阶梯确认（形态 + EMA）
  3. 有信号按级别通过飞书报警

用法:
  python main.py            # 持续运行（每小时 58 分触发）
  python main.py --once     # 立即扫描一次（测试用）
"""
import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta

import yaml

# 将项目根目录加入路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.exchange_client import ExchangeClient
from core.signal_screener import SignalScreener
from core.feishu_alert import FeishuAlert

# ---------- 日志 ----------
def setup_logging():
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "system.log")

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    return log_file


def load_config(path="config.yaml"):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def seconds_until_next_scan(minute=58):
    """计算距离下一次触发（每小时 minute 分）还有多少秒"""
    now = datetime.now()
    nxt = now.replace(minute=minute, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(hours=1)
    return (nxt - now).total_seconds()


def resolve_scan_slot(target_minute, window):
    """
    根据当前时刻判断本次触发该扫哪根 K 线。
    GitHub Actions 的 cron 会被延迟/丢弃，因此窗口设得比触发点宽，
    让窗口内的任意一次触发都能补上同一轮扫描。

    返回 (slot 标识, 是否丢弃最后一根未收盘K线)，不在窗口内返回 (None, False)
    """
    now = datetime.now()
    if (now.minute - target_minute) % 60 >= window:
        return None, False
    if now.minute >= target_minute:
        # 收盘前：扫当前未收盘 K 线
        return now.strftime("%Y%m%d%H"), False
    # 已跨整点（补扫）：丢掉刚开盘的新 K 线，扫刚收盘的那根
    return (now - timedelta(hours=1)).strftime("%Y%m%d%H"), True


def read_last_slot(state_dir):
    """读取上次已扫描的 K 线标识"""
    path = os.path.join(state_dir, "last_slot.txt")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return f.read().strip() or None


def write_last_slot(state_dir, slot):
    """写入本次已扫描的 K 线标识"""
    os.makedirs(state_dir, exist_ok=True)
    with open(os.path.join(state_dir, "last_slot.txt"), "w", encoding="utf-8") as f:
        f.write(slot)


class AlertCounter:
    """
    报警计数器（无去重）：
      - 同一 (symbol, direction) 每轮报警次数 +1
      - 信号消失后重新计数（重现从第 1 次开始）
    """

    def __init__(self):
        self._count = {}  # (symbol, direction) -> 第几次报警

    def next_count(self, symbol, direction):
        """累加并返回当前是第几次报警"""
        key = (symbol, direction)
        self._count[key] = self._count.get(key, 0) + 1
        return self._count[key]

    def clear_after_scan(self, active_keys):
        """扫描完成后清理已消失的信号（重现时重新从 1 计数）"""
        for key in list(self._count.keys()):
            if key not in active_keys:
                del self._count[key]


def run_scan(config, logger, drop_last=False):
    """执行一次完整扫描，返回发现的信号列表"""
    signals = []

    # 连接交易所
    ex = ExchangeClient(
        priority=config["exchange"]["priority"],
        market_type=config["exchange"]["market_type"],
        proxy=config["exchange"].get("proxy", ""),
    )
    try:
        ex.connect()
    except ConnectionError as e:
        logger.error("❌ %s", e)
        return signals

    # 获取成交量前 N
    try:
        top = ex.fetch_top_symbols(config["exchange"]["top_n"])
    except ConnectionError as e:
        logger.error("❌ 获取交易对失败: %s", e)
        return signals
    if not top:
        logger.warning("⚠️ 没有获取到交易对")
        return signals

    # 扫描
    screener = SignalScreener(config, ex, drop_last=drop_last)
    logger.info("🔍 开始扫描 %d 个交易对（%s）...", len(top), ex.active_exchange)
    results = screener.scan_all(top)
    logger.info("📡 扫描完成，共发现 %d 个信号", len(results))

    for symbol, level, report in results:
        report["exchange"] = ex.active_exchange
        signals.append((symbol, level, report))
        logger.info("🚨 %s 级别%d %s [%s] 形态=%s",
                    symbol, level, report["direction_label"],
                    report.get("source", "?"),
                    report["levels"].get("1h",
                        report["levels"].get("4h",
                            report["levels"].get("8h", {}))).get("patterns", []))

    return signals


def main():
    parser = argparse.ArgumentParser(description="多周期确认系统提醒")
    parser.add_argument("--once", action="store_true", help="立即扫描一次后退出")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--window", type=int, default=0,
                        help="扫描窗口（分钟）。>0 时仅在目标分钟附近触发才执行，用于定时任务兜底")
    parser.add_argument("--state-dir", default="",
                        help="状态目录，用于同一根K线去重（配合 --window 使用）")
    args = parser.parse_args()

    log_file = setup_logging()
    logger = logging.getLogger("main")
    logger.info("🚀 多周期确认系统提醒启动")

    config = load_config(args.config)
    feishu = FeishuAlert(config.get("feishu", {}).get("webhook_url", ""))
    if not feishu.enabled:
        logger.warning("⚠️ 飞书 Webhook 未配置！请在 config.yaml 的 feishu.webhook_url 配置，")
        logger.warning("   或设置环境变量 FEISHU_WEBHOOK_URL")
    counter = AlertCounter()

    scan_minute = config["schedule"]["minute"]

    # 定时兜底模式：GitHub Actions 的 cron 会被延迟甚至丢弃，
    # 因此用高频触发 + 较宽窗口 + 同一根K线去重，保证每小时不漏扫。
    drop_last = False
    slot = None
    if args.window > 0:
        slot, drop_last = resolve_scan_slot(scan_minute, args.window)
        if slot is None:
            logger.info("⏭️ 当前不在扫描窗口（目标 %02d 分），跳过本次触发", scan_minute)
            return
        if args.state_dir and read_last_slot(args.state_dir) == slot:
            logger.info("⏭️ K线 %s 已扫描过，跳过重复触发", slot)
            return
        logger.info("✅ 命中扫描窗口，本轮扫描 K线 %s%s",
                    slot, "（补扫已收盘K线）" if drop_last else "")

    if args.once:
        # 测试模式：立即扫一次
        logger.info("▶️ 测试模式：立即执行一次扫描")
        signals = run_scan(config, logger, drop_last=drop_last)
        for symbol, level, report in signals:
            report["alert_count"] = counter.next_count(symbol, report["direction"])
        if signals:
            feishu.send_summary(signals)
        if args.state_dir and slot:
            write_last_slot(args.state_dir, slot)
        logger.info("🏁 测试扫描结束，共 %d 个信号，日志: %s", len(signals), log_file)
        return

    # 持续运行
    logger.info("⏰ 已启动，将在每小时 %02d 分自动扫描（日志: %s）", scan_minute, log_file)
    while True:
        wait_sec = seconds_until_next_scan(scan_minute)
        logger.info("😴 距下次扫描还有 %d 秒（%s）",
                    int(wait_sec), (datetime.now() + timedelta(seconds=wait_sec)).strftime("%H:%M:%S"))
        time.sleep(min(wait_sec, 60))  # 每 60 秒醒来检查一次，避免错过
        if wait_sec > 60:
            continue

        # 触发扫描
        try:
            signals = run_scan(config, logger)
            active_keys = set()
            for symbol, level, report in signals:
                direction = report["direction"]
                active_keys.add((symbol, direction))
                report["alert_count"] = counter.next_count(symbol, direction)
            counter.clear_after_scan(active_keys)
            # 有信号就发汇总卡片（不去重，交易对后标注第几次报警）
            if signals:
                feishu.send_summary(signals)
            else:
                logger.info("😴 本轮无信号")
        except KeyboardInterrupt:
            logger.info("🛑 收到退出信号，系统停止")
            break
        except Exception as e:
            logger.exception("❌ 扫描过程异常: %s", e)

        # 防止同一分钟内重复触发
        time.sleep(70)


if __name__ == "__main__":
    main()
