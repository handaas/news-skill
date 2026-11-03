# news-skill：已验收示例

本目录只展示 **5 个完全通过的真实正向场景**（最低要求4例）。

每例均为真实、非空、非缓存调用；技术检查、独立业务检查和离线浏览器检查全部通过。报告保持原实测字节，不把空数据、报错处理或仅生成页面计作通过。

| 场景 | 真实使用需求 | 报告 |
|---|---|---|
| [品牌声誉总览](01-reputation-overview/README.md) | 生成比亚迪股份有限公司的舆情统计、情感趋势和明细报告。 | [HTML](01-reputation-overview/report.html) / [MD](01-reputation-overview/report.md) / [JSON](01-reputation-overview/report.json) |
| [负面舆情专项监测](02-negative-watch/README.md) | 筛选华为技术有限公司的负面舆情明细并保留总体情感统计。 | [HTML](02-negative-watch/report.html) / [MD](02-negative-watch/report.md) / [JSON](02-negative-watch/report.json) |
| [正面传播效果抽样](03-positive-coverage/README.md) | 查看贵州茅台酒股份有限公司的正面舆情样本和趋势。 | [HTML](03-positive-coverage/report.html) / [MD](03-positive-coverage/report.md) / [JSON](03-positive-coverage/report.json) |
| [中性行业报道跟踪](04-neutral-watch/README.md) | 抽取宁德时代新能源科技股份有限公司的中性舆情，观察主题变化。 | [HTML](04-neutral-watch/report.html) / [MD](04-neutral-watch/report.md) / [JSON](04-neutral-watch/report.json) |
| [舆情管理层快报](07-compact-news/README.md) | 为北京京东世纪贸易有限公司生成前 5 条舆情快报。 | [HTML](07-compact-news/report.html) / [MD](07-compact-news/report.md) / [JSON](07-compact-news/report.json) |

- [逐例验收结果](results.json) · [业务复核](review.json) · [证据摘要](evidence-index.json)
- [完整测试记录](../test-records/live-replaced-20261027/README.md)仅用于审计，不属于可展示示例。
- 当前数据与结论限于原查询时点及公开样本；覆盖率不代表准确率或低风险。
