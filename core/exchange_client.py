# -*- coding: utf-8 -*-
"""
exchange_client.py - 交易所连接层
按优先级连接 Binance → Gate.io → OKX（USDT 永续合约）
出问题自动切换下一个交易所
"""
import logging
import ccxt
import pandas as pd

logger = logging.getLogger("exchange")

# 需要排除的稳定币/特殊交易对
EXCLUDE_SYMBOLS = {"USDC/USDT:USDT", "TUSD/USDT:USDT", "FDUSD/USDT:USDT",
                   "DAI/USDT:USDT", "BUSD/USDT:USDT", "USDP/USDT:USDT"}


class ExchangeClient:
    """交易所客户端，支持多交易所自动故障切换"""

    def __init__(self, priority, market_type="swap", proxy=""):
        self.priority = priority
        self.market_type = market_type
        self.proxy = proxy
        self._client = None
        self._active_exchange = None
        self._markets = None

    def _create_client(self, exchange_id):
        """创建交易所客户端实例"""
        exchange_class = getattr(ccxt, exchange_id)
        params = {
            "enableRateLimit": True,
            "timeout": 20000,
            "options": {"defaultType": self.market_type},
        }
        if self.proxy:
            params["proxies"] = {"http": self.proxy, "https": self.proxy}
        return exchange_class(params)

    def connect(self):
        """依次尝试连接交易所，返回当前活跃的客户端"""
        for exchange_id in self.priority:
            try:
                client = self._create_client(exchange_id)
                # 测试连通性：加载市场数据
                client.load_markets()
                self._client = client
                self._active_exchange = exchange_id
                logger.info("✅ 已连接交易所: %s", exchange_id)
                return client
            except Exception as e:
                logger.warning("⚠️ 交易所 %s 连接失败: %s，尝试下一个...", exchange_id, e)
        raise ConnectionError("所有交易所均连接失败，请检查网络或代理配置")

    @property
    def active_exchange(self):
        return self._active_exchange

    def get_swap_usdt_markets(self):
        """获取 USDT 永续合约市场列表（过滤稳定币）"""
        if self._markets is None:
            markets = []
            for symbol, m in self._client.markets.items():
                if (m.get("swap") and m.get("quote") == "USDT"
                        and m.get("settle") == "USDT"
                        and symbol not in EXCLUDE_SYMBOLS):
                    markets.append(m)
            self._markets = markets
        return self._markets

    def fetch_top_symbols(self, top_n=50):
        """
        获取成交量前 top_n 的 USDT 永续交易对
        按 24h 成交额（quoteVolume）排序
        """
        # 先构建合法市场集合（USDT 永续），确保过滤生效
        valid_markets = self.get_swap_usdt_markets()
        valid_symbols = {m["symbol"] for m in valid_markets}

        try:
            tickers = self._client.fetch_tickers()
        except Exception as e:
            raise ConnectionError(f"获取 tickers 失败: {e}")

        records = []
        for symbol, t in tickers.items():
            if t.get("quoteVolume") is None:
                continue
            if symbol not in valid_symbols:
                continue
            records.append({
                "symbol": symbol,
                "quoteVolume": float(t["quoteVolume"]),
                "last": t.get("last"),
            })

        records.sort(key=lambda x: x["quoteVolume"], reverse=True)
        top = records[:top_n]
        logger.info("📊 %s 成交量前 %d: %s", self._active_exchange, len(top),
                    ", ".join(r["symbol"] for r in top[:10]) + (" ..." if len(top) > 10 else ""))
        return top

    def _is_valid_symbol(self, symbol):
        """判断 symbol 是否在合法市场中"""
        if self._markets is None:
            return symbol in self._client.markets
        return symbol in {m["symbol"] for m in self._markets}

    def fetch_ohlcv(self, symbol, timeframe, limit=200):
        """
        获取 K 线数据，返回 DataFrame: [timestamp, open, high, low, close, volume]
        最后一行是当前未收盘的 K 线
        """
        raw = self._client.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        return df

    def switch_exchange(self):
        """主动切换到下一个交易所"""
        current_idx = self.priority.index(self._active_exchange)
        remaining = self.priority[current_idx + 1:]
        if not remaining:
            return False
        self._markets = None
        for exchange_id in remaining:
            try:
                client = self._create_client(exchange_id)
                client.load_markets()
                self._client = client
                self._active_exchange = exchange_id
                logger.info("🔄 已切换到交易所: %s", exchange_id)
                return True
            except Exception as e:
                logger.warning("⚠️ 交易所 %s 连接失败: %s", exchange_id, e)
        return False
