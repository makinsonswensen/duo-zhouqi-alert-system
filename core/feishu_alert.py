# -*- coding: utf-8 -*-
"""
feishu_alert.py - 飞书机器人报警
支持两种发送模式:
  汇总模式（默认）: 一轮扫描的所有信号按逻辑来源分类，合并成一张卡片发送
  逐条模式: 每个信号单独一张卡片（旧模式，信号多时较吵）
"""
import json
import logging
import os
from datetime import datetime

import requests

logger = logging.getLogger("feishu")

# 报警级别 → 卡片模板颜色 / 标题
LEVEL_STYLE = {
    1: {"template": "orange",  "title": "普通报警 · 1h 信号确认"},
    2: {"template": "red",     "title": "强烈报警 · 双周期共振 / 直接周期信号"},
    3: {"template": "carmine", "title": "剧烈报警 · 1h+4h+8h 三周期共振"},
}

# 方向 → 展示（红涨绿跌，国内惯例）
DIR_DISPLAY = {
    "up":   "<font color='red'>▲多头</font>",
    "down": "<font color='green'>▼空头</font>",
}
DIR_PLAIN = {"up": "▲多头", "down": "▼空头"}


def _short_symbol(symbol):
    """BTC/USDT:USDT → BTC/USDT，缩短显示"""
    return symbol.replace(":USDT", "")


class FeishuAlert:
    def __init__(self, webhook_url=None):
        # 优先级: 环境变量 > 参数 > 空
        # （环境变量优先：云环境运行时 config.yaml 由脱敏模板生成，
        #   其占位符不应覆盖真实 Secret）
        self.webhook_url = (os.environ.get("FEISHU_WEBHOOK_URL") or webhook_url or "").strip()
        if not self.webhook_url:
            logger.warning("⚠️ 未配置飞书 Webhook，报警消息将只写入日志")

    @property
    def enabled(self):
        return bool(self.webhook_url)

    # ============ 汇总模式 ============

    def send_summary(self, signals):
        """
        分类汇总发送一张卡片。
        signals: [(symbol, level, report), ...]
        """
        if not signals:
            return False
        payload = self._build_summary_card(signals)
        return self._post(payload)

    def _classify(self, signals):
        """按逻辑来源 + 方向分类"""
        c = {
            "l3": [],                                          # 阶梯·三周期共振
            "l2": [],                                          # 阶梯·双周期共振
            "l1_up": [], "l1_down": [],                        # 阶梯·1h 信号
            "d4_up": [], "d4_down": [],                        # 直接4h
            "d8_up": [], "d8_down": [],                        # 直接8h
        }
        for symbol, level, report in signals:
            src = report.get("source", "阶梯确认")
            d = "up" if report.get("direction") == "up" else "down"
            if src == "阶梯确认":
                if level >= 3:
                    c["l3"].append(report)
                elif level == 2:
                    c["l2"].append(report)
                else:
                    c[f"l1_{d}"].append(report)
            elif src == "直接4h":
                c[f"d4_{d}"].append(report)
            elif src == "直接8h":
                c[f"d8_{d}"].append(report)

        # 每个分类内按报警次数从大到小排序：持续越久的越靠前，第1次（无计数）排最后
        for key in c:
            c[key].sort(key=lambda r: (r.get("alert_count") or 0), reverse=True)
        return c

    def _patterns_of(self, report):
        """取信号中第一个有形态的周期，返回形态字符串"""
        for tf in ("1h", "4h", "8h"):
            if tf in report.get("levels", {}):
                pats = report["levels"][tf].get("patterns", [])
                if pats:
                    return "、".join(pats)
        return "-"

    def _close_of(self, report):
        """取现价"""
        for tf in ("1h", "4h", "8h"):
            if tf in report.get("levels", {}):
                c = report["levels"][tf].get("close")
                if c is not None:
                    return c
        return None

    def _fmt_price(self, price):
        """价格格式化：大数留2位小数，小数留4位，微小价格去尾零"""
        if price is None:
            return "-"
        if price >= 100:
            return f"{price:,.2f}"
        if price >= 1:
            return f"{price:.4f}"
        s = f"{price:.6f}".rstrip("0")
        return s if s else "0"

    def _fmt_count(self, report):
        """报警次数标注：第N次持续报警（第1次报警不显示，无计数信息也不显示）"""
        n = report.get("alert_count")
        if n is None or n <= 1:
            return ""
        return f"第{n}次持续报警"

    def _fmt_detail_line(self, report):
        """详细行：Level 2/3 信号用，展示各周期形态链 + 现价 + 持续报警次数"""
        sym = _short_symbol(report["symbol"])
        d = DIR_DISPLAY.get(report["direction"], "")
        cnt = self._fmt_count(report)
        chain = []
        for tf in ("1h", "4h", "8h"):
            if tf in report.get("levels", {}):
                pats = "、".join(report["levels"][tf].get("patterns", [])) or "-"
                chain.append(f"{tf} {pats}")
        price = self._fmt_price(self._close_of(report))
        cnt_str = f" {cnt}" if cnt else ""
        return f"**{sym}** {d}：{' → '.join(chain)}（现价 {price}）{cnt_str}"

    def _fmt_compact_group(self, reports, direction):
        """紧凑分组行：Level 1 / 直接周期信号用，形态后标注持续报警次数（第1次不标注）"""
        items = []
        for r in reports:
            sym = _short_symbol(r["symbol"])
            cnt = self._fmt_count(r)
            items.append(f"{sym}（{self._patterns_of(r)}）{cnt}")
        label = DIR_PLAIN.get(direction, direction)
        return f"{label}（{len(reports)}）：{'、'.join(items)}"

    def _build_summary_card(self, signals):
        """构造分类汇总卡片"""
        groups = self._classify(signals)
        max_level = max(lvl for _, lvl, _ in signals)
        style = LEVEL_STYLE.get(max_level, LEVEL_STYLE[1])
        exchange = signals[0][2].get("exchange", "")
        n = len(signals)

        # 标题按最高级别动态变化
        if max_level >= 3:
            title = f"🚨🚨 剧烈报警 · 三周期共振 · 共 {n} 个信号"
            template = "carmine"
        elif max_level == 2:
            title = f"🚨 强烈报警 · 共 {n} 个信号"
            template = "red"
        else:
            title = f"📊 信号汇总 · {n} 个 1h 信号"
            template = "orange"

        elements = []

        def add_section(header, body_lines):
            """添加一个分类区块"""
            if not body_lines:
                return
            content = f"**{header}**\n" + "\n".join(body_lines)
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": content},
            })
            elements.append({"tag": "hr"})

        # ① 三周期共振（详细行）
        lines = [self._fmt_detail_line(r) for r in groups["l3"]]
        add_section(f"🔴 三周期共振（1h+4h+8h）· {len(groups['l3'])} 个", lines)

        # ② 双周期共振（详细行）
        lines = [self._fmt_detail_line(r) for r in groups["l2"]]
        add_section(f"🟠 双周期共振（1h+4h）· {len(groups['l2'])} 个", lines)

        # ③ 1h 阶梯信号（紧凑分组）
        cnt = len(groups["l1_up"]) + len(groups["l1_down"])
        lines = []
        if groups["l1_up"]:
            lines.append(self._fmt_compact_group(groups["l1_up"], "up"))
        if groups["l1_down"]:
            lines.append(self._fmt_compact_group(groups["l1_down"], "down"))
        add_section(f"⚡ 1h 阶梯信号 · {cnt} 个", lines)

        # ④ 直接4h筛选（紧凑分组）
        cnt = len(groups["d4_up"]) + len(groups["d4_down"])
        lines = []
        if groups["d4_up"]:
            lines.append(self._fmt_compact_group(groups["d4_up"], "up"))
        if groups["d4_down"]:
            lines.append(self._fmt_compact_group(groups["d4_down"], "down"))
        add_section(f"🔷 直接4h筛选 · {cnt} 个", lines)

        # ⑤ 直接8h筛选（紧凑分组）
        cnt = len(groups["d8_up"]) + len(groups["d8_down"])
        lines = []
        if groups["d8_up"]:
            lines.append(self._fmt_compact_group(groups["d8_up"], "up"))
        if groups["d8_down"]:
            lines.append(self._fmt_compact_group(groups["d8_down"], "down"))
        add_section(f"🔶 直接8h筛选 · {cnt} 个", lines)

        # 底部备注
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        note = f"数据源: {exchange or '未知'} · {now} · 不构成投资建议"
        elements.append({
            "tag": "note",
            "elements": [{"tag": "plain_text", "content": note}],
        })

        return {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": template,
                },
                "elements": elements,
            },
        }

    # ============ 逐条模式（旧模式，保留兼容） ============

    def send_level_alert(self, level, signal):
        """发送指定级别的报警卡片（逐条模式）"""
        if level not in LEVEL_STYLE:
            return False
        style = LEVEL_STYLE[level]
        payload = self._build_card(style["template"], style["title"], level, signal)
        return self._post(payload)

    def _build_card(self, template, title, level, signal):
        """构造飞书卡片消息"""
        direction = signal.get("direction_label", "")
        symbol = signal.get("symbol", "")
        source = signal.get("source", "阶梯确认")

        elements = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**交易对：** {symbol}\n**方向：** {direction}\n"
                               f"**信号来源：** {source}\n**来源交易所：** {signal.get('exchange', '未知')}"
                },
            },
            {"tag": "hr"},
        ]

        # 各周期详情（支持阶梯和直接两种报告格式）
        level_order = ["1h", "4h", "8h"]
        for tf in level_order:
            if tf in signal.get("levels", {}):
                info = signal["levels"][tf]
                patterns_str = "、".join(info["patterns"]) if info["patterns"] else "-"
                ema_str = "，".join(f"EMA{p}={v:.2f}" for p, v in info["ema"].items())
                close = info.get("close")
                close_str = f"{close:.2f}" if close is not None else "-"
                elements.append({
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": f"**{tf} 周期：**\n形态：{patterns_str}\n"
                                   f"收盘：{close_str}\n{ema_str}"
                    },
                })

        elements.append({"tag": "hr"})
        elements.append({
            "tag": "note",
            "elements": [{
                "tag": "plain_text",
                "content": "多周期确认系统提醒 · 信号仅作参考，不构成投资建议",
            }],
        })

        return {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": template,
                },
                "elements": elements,
            },
        }

    # ============ 底层发送 ============

    def _post(self, payload):
        """发送消息到飞书"""
        if not self.enabled:
            logger.info("[飞书未配置，模拟发送] %s", json.dumps(payload, ensure_ascii=False)[:200])
            return True
        try:
            resp = requests.post(self.webhook_url, json=payload, timeout=10)
            data = resp.json()
            if data.get("code") == 0:
                logger.info("✅ 飞书报警发送成功")
                return True
            else:
                logger.error("❌ 飞书报警发送失败: %s", data)
                return False
        except Exception as e:
            logger.error("❌ 飞书请求异常: %s", e)
            return False
