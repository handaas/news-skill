# MCP 工具参考 — news-mcp-server

本 skill 连接的 MCP server：`handaas-mcp-server/news-mcp-server`（“舆情大数据”）。

> **重要**：舆情详情类工具（stats / list）入参为 `matchKeyword`（**企业全称** / 注册号 / 统一社会信用代码 / 企业 id）+ `keywordType`；
> 当用户只给企业关键词时，必须先调关键词模糊查询补全全称。

## 通用约定

- `keywordType` 枚举：`name`（企业名称）/ `nameId`（企业 id）/ `regNumber`（注册号）/ `socialCreditCode`（统一社会信用代码）。
- `sentimentLabel` 枚举：`0`=负面 / `1`=正面 / `2`=中性 / `3`=未知。
- 分页：`pageIndex` 从 1 开始；`pageSize` 单页最多 50。
- 统计返回的情感分布键：`neutral`（中立）/ `negative`（消极）/ `positive`（积极）/ `unknown`（未知）。

---

## 工具清单

### 1. `news_bigdata_fuzzy_search` — 关键词模糊查询企业

用途：根据企业名称 / 人名 / 品牌 / 产品 / 岗位等关键词模糊查询企业列表，用于补全企业全称。

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `matchKeyword` | string | 是 | 匹配关键词 |
| `pageIndex` | int | 否 | 分页开始位置 |
| `pageSize` | int | 否 | 单页最多 50 |

返回：`total` + 企业列表（`name`、`nameId`、`regCapitalValue`、`foundTime`、`operStatus`、`address`、`legalRepresentative`、`enterpriseType`、`catchReason` 命中原因等）。

product_id：`675cea1f0e009a9ea37edaa1`。

---

### 2. `news_bigdata_news_stats` — 舆情统计

用途：按企业主体返回舆情情感类型统计（消极/中立/积极/未知）及其趋势变化，用于声誉管理与舆情监控。

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `matchKeyword` | string | 是 | 企业名称 / 注册号 / 统一社会信用代码 / 企业 id（无全称则先调 fuzzy_search） |
| `keywordType` | string | 否 | 主体类型：name / nameId / regNumber / socialCreditCode |

返回：`newsSentimentStats`（情感类型统计 dict：neutral/negative/positive/unknown）、`sentimentLabelList`（情感类别 list）、`newsSentimentTrend`（趋势：month + stats{negative/positive}）。

product_id：`66b338e274bf098447db7efd`。

---

### 3. `news_bigdata_news_list` — 舆情列表

用途：按企业主体查询新闻舆情明细，可按情感类别过滤。

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `matchKeyword` | string | 是 | 企业名称 / 注册号 / 统一社会信用代码 / 企业 id |
| `keywordType` | string | 否 | 主体类型：name / nameId / regNumber / socialCreditCode |
| `pageIndex` | int | 否 | 从 1 开始（默认 1） |
| `pageSize` | int | 否 | 单页最多 50（默认 50） |
| `sentimentLabel` | int | 否 | 舆情类别：0=负面 / 1=正面 / 2=中性 / 3=未知 |

返回（list + `total`）：每条含 `newsTitle`（标题）、`newsSource`（来源）、`newsPublishTime`（发布时间）、`newsLink`（链接）、`newsBrief`（简介）、`relatedEnterprises`（相关企业）、`sentimentLabel`（舆情类别）。

product_id：`66b485eadaf8c77fb249a455`。

---

## 推荐调用顺序（报告编排）

1. （若仅有关键词）`news_bigdata_fuzzy_search` → 取 `name` 作为全称。
2. `news_bigdata_news_stats` → 情感分布与趋势统计。
3. `news_bigdata_news_list` → 舆情明细（按需可加 `sentimentLabel` 过滤）。

> 单次报告通常调用 2-3 个工具；stats / list 入参均为企业主体 `matchKeyword` + `keywordType`。
