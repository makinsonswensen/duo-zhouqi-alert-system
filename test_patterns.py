# -*- coding: utf-8 -*-
"""形态识别模块自测：构造已知形态的 K 线验证识别正确性"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
from core.pattern_detector import detect_patterns

def make_df(candles):
    """candles: [(open, high, low, close), ...]"""
    rows = []
    for i, (o, h, l, c) in enumerate(candles):
        rows.append({"timestamp": i * 3600000, "open": o, "high": h, "low": l, "close": c, "volume": 100})
    return pd.DataFrame(rows)

def run(name, candles, expect):
    df = make_df(candles)
    res = detect_patterns(df)
    got = [(p["direction"], p["pattern"]) for p in res]
    ok = expect in got
    print(f"{'✅' if ok else '❌'} {name}: 期望={expect} 实际={got}")
    return ok

results = []

# 锤子线: 长下影 短上影 实体在上部
# 最后一根: open=100 high=103 low=90 close=102
results.append(run("锤子线", [(100,105,99,101),(101,103,98,100),(100,103,90,102)],
                   ("up", "锤子线")))

# 射击之星: 长上影 短下影 实体在下部
# 最后一根: open=102 high=112 low=99 close=100
results.append(run("射击之星", [(100,105,99,101),(101,103,98,100),(102,112,99,100)],
                   ("down", "射击之星")))

# 看涨吞没: 前阴后阳, 阳实体包住阴实体
# 前根: o=102 c=98 阴线; 后根: o=97 c=104 阳线
results.append(run("看涨吞没", [(100,105,99,101),(102,102,98,98),(97,104,96,104)],
                   ("up", "看涨吞没")))

# 看跌吞没: 前阳后阴, 阴实体包住阳实体
results.append(run("看跌吞没", [(100,105,99,101),(98,102,97,102),(103,103,96,96)],
                   ("down", "看跌吞没")))

# 早晨之星: 大阴 → 小实体 → 大阳(收盘>前实体中点)
results.append(run("早晨之星", [(110,110,100,100),(100,101,99,100),(100,108,99,107)],
                   ("up", "早晨之星")))

# 黄昏之星: 大阳 → 小实体 → 大阴(收盘<前实体中点)
results.append(run("黄昏之星", [(100,110,100,110),(110,111,109,110),(110,110,101,101)],
                   ("down", "黄昏之星")))

# 刺透形态: 阴线后阳线开盘破前低 收盘入前实体上半部
results.append(run("刺透形态", [(100,105,99,101),(104,104,99,99),(98,105,97,102)],
                   ("up", "刺透形态")))

# 乌云盖顶: 阳线后阴线开盘破前高 收盘入前实体下半部
results.append(run("乌云盖顶", [(100,105,99,101),(99,103,98,103),(104,104,99,100)],
                   ("down", "乌云盖顶")))

# 多头孕线: 大阴后小阳 实体在阴线实体内
results.append(run("多头孕线", [(100,105,99,101),(103,103,98,98),(98.5,100.5,98.4,99.5)],
                   ("up", "多头孕线")))

# 空头孕线: 大阳后小阴 实体在阳线实体内
results.append(run("空头孕线", [(100,105,99,101),(98,102,97,102),(101,101,99,100)],
                   ("down", "空头孕线")))

# 无形态的情况（普通震荡）不应报错
noise = detect_patterns(make_df([(100,102,98,101),(101,103,99,102),(102,104,100,103)]))
print(f"{'✅' if not noise else '❌'} 无形态不误报: {noise}")
results.append(not noise)

print(f"\n总结果: {sum(results)}/{len(results)} 通过")
sys.exit(0 if all(results) else 1)
