# Trend Radar

新词日报 + 趋势事件证据链。方法论：发现异常 → 寻找源头 → 验证传播 → 小成本试错。

## 结构

```
trend-radar/
  scripts/
    trends.py         # Google Trends API 客户端（仅标准库）
    collect.py        # 词根采集 → 候选池 → 历史复核 → 硬规则分类
    cluster.py        # 事件簇构建（变体/源头/时间线/阶段/假设标注）
    report.py         # 输出 site/data/daily.json + reports/YYYY-MM-DD.md
    test_pipeline.py  # 离线回归测试（合成夹具，无网络）
  state/
    roots.json        # 526 词根池（固定）
    state.json        # 运行缓存（自动生成，不提交真实凭据）
    clusters.json     # 事件簇（自动生成）
  site/data/daily.json
  reports/YYYY-MM-DD.md
  experiments.json    # 建站实验账本
```

## 运行

```bash
python3 -B scripts/test_pipeline.py                      # 先跑离线测试
python3 -B scripts/collect.py --root-limit 526 --review-limit 2004
python3 -B scripts/cluster.py
python3 -B scripts/report.py
```

增量运行时去掉 `--root-limit/--review-limit`（默认按最久未更新轮换）。

## 方法口径

- 采集：每词根 `now 7-d` + `now 1-d` 的 Related Queries → Rising，全球。
- 候选池：全部上升词去重后**全部**进入候选；Breakout / ≥1000% 只是展示子集。
- 复核：`today 12-m`（首次出现/基线）、`today 1-m`（30天日线）、`now 7-d` vs gpts（同图相对倍数）、`today 5-y`（一年前历史）。
- 分类硬规则：新词（起量前峰值 < 近6周峰值1% 且无5年历史）、老词二次爆火（有1年前历史 或 近期峰值 ≥ 前期中位数5倍）、短时尖峰（30天1-2个活跃日且已回落）、其余待观察。
- 娱乐性词汇统一进入“待观察”，同时保留其原始趋势复核结论，避免把短周期热闹误当成建站机会。
- 默认按涨幅降序；页面可切换为按近 7 天 `vs gpts` 相对搜索量降序。该值来自同图对齐点总量之比，不是绝对月搜索量。
- 事件簇合并依据：实体ID > 共同源头URL > 来源页共现 > 上游别名 > 人工确认；**禁止纯字符串相似度自动合并**。
- 阶段：S0 detected → S1 origin-linked → S2 spreading → S3 search-rising → S4 serp-validated → S5 page-testing → S6 search-converted。

## 运行假设（H1–H4）

关于 AI 发现/抓取/推荐机制的四条假设作为**运行假设**参与排序与实验设计，但必须标注为 `hypothesis`，与实测信号分开显示；无数据时写"未知"，不得写成零，不得写成官方事实。

## 数据边界

- Trends 数值是同图 0~100 相对热度，不是搜索量；vs gpts 是相对倍数。
- 滚动窗口的历史回放与采集当时不一定是同一快照；跨日期比较只能算探索性比较。
- HTTP 429 按 `Retry-After` 或有限指数退避重试 2 次；仍失败才停止本轮，不并发、不把失败写成零，保留上一份成功输出。
