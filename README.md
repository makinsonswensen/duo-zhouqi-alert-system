# 多周期确认系统提醒

基于经典 K 线形态 + EMA 过滤的多周期阶梯确认预警系统。

## 策略逻辑

```
每根 1h K 线第 58 分钟触发扫描（当前 K 线即将收盘）

一级（普通报警）: 1h 形态 + 收盘在 EMA7/EMA25 之上(多) / 之下(空)
    └─ 成立 → 检查 4h
二级（强烈报警）: 4h 形态 + 收盘在 EMA7 之上(多) / 之下(空)，方向一致
    └─ 成立 → 检查 8h
三级（剧烈报警）: 8h 形态 + 收盘在 EMA7 之上(多) / 之下(空)，方向一致
```

方向一致递进（方案A）：只有多周期共振才升级报警，防止逆势信号。

## 识别形态（10 种）

| 方向 | 形态 |
|------|------|
| 多头 | 锤子线、看涨吞没、早晨之星、刺透形态、多头孕线 |
| 空头 | 射击之星、看跌吞没、黄昏之星、乌云盖顶、空头孕线 |

## 目录结构

```
多周期确认系统提醒/
├── main.py                    # 主控入口
├── config.yaml                # 配置文件
├── requirements.txt           # Python 依赖
├── test_patterns.py           # 形态识别自测
├── core/
│   ├── exchange_client.py     # 交易所连接层（Binance→Gate→OKX 自动切换）
│   ├── pattern_detector.py    # K 线形态识别
│   ├── signal_filters.py      # 信号质量过滤（成交量确认 + K线幅度）
│   ├── signal_screener.py     # 多周期信号筛选
│   └── feishu_alert.py        # 飞书报警（汇总卡片）
└── logs/
    └── system.log             # 运行日志
```

## 安装与运行

### 1. 安装依赖

```bash
# 使用受管 Python 环境（已配置 venv: multi_tf）
C:/Users/Admin/.workbuddy/binaries/python/envs/multi_tf/Scripts/python.exe -m pip install -r requirements.txt
```

### 2. 配置飞书 Webhook

编辑 `config.yaml`：

```yaml
feishu:
  webhook_url: "https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx"
```

或设置环境变量 `FEISHU_WEBHOOK_URL`。

### 3. 运行

```bash
# 测试模式：立即扫描一次
python main.py --once

# 持续运行：每小时第 58 分钟自动扫描
python main.py
```

## 配置说明（config.yaml）

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `exchange.priority` | 交易所优先级 | `[binance, gateio, okx]` |
| `exchange.top_n` | 成交量前 N 交易对 | `50` |
| `timeframes.level1/2/3` | 三级周期 | `1h / 4h / 8h` |
| `timeframes.ema` | 各级 EMA 参数 | L1: [7,25], L2: [7], L3: [7] |
| `schedule.minute` | 每小时触发分钟 | `58` |
| `signal_filters.volume` | 成交量确认：当根量 ≥ 前20根均量 × 1.5 | 开启 |
| `signal_filters.range` | K线幅度过滤：当根振幅 ≥ ATR14 × 0.5 | 开启 |
| `feishu.webhook_url` | 飞书机器人地址 | 空 |

## 报警去重

同一交易对同一方向，只有信号**升级**（1→2→3 级）时才再次报警；信号消失后重新出现会再次触发，避免每小时刷屏。

## 注意事项

- 数据源为 USDT 永续合约（swap），自动过滤 USDC 等非 USDT 本位交易对
- Binance 不可达时自动切换 Gate.io → OKX
- 信号仅作参考，不构成投资建议
