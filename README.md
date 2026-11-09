# 舆情大数据报告

一个可被本地智能体使用的 HandaaS 舆情大数据 Skill。用户只需要说"使用舆情大数据，分析广州探迹科技有限公司的舆情情感分布与媒体来源"，智能体会自动连接 MCP 服务、获取原始数据并编排为结构化报告（HTML + Markdown + JSON + PDF 四件套），内嵌 ECharts 5.x 可视化图表，离线可打开。

> Skill 本身不托管用户数据或凭证。推荐在旷湖SKILL平台一键创建所需在线 MCP 并使用本 Skill 专属 Token；Token 仅保存在本机私有配置并注入运行进程。仅选择本地部署时，底层接口凭证才由 MCP server 自己的 `.env` 持有。

## 目录

- [一句话安装](#一句话安装)
- [快速验证](#快速验证)
- [接入 MCP 服务](#接入-mcp-服务)
- [生成报告](#生成报告)
- [效果预览](#效果预览)
- [命令行用法](#命令行用法)
- [故障排查](#故障排查)

## 一句话安装

在**旷湖SKILL平台**为本 Skill 创建所需在线 MCP 服务后，复制下面整段发给智能体，只替换最后一行的 Token：

```text
请帮我安装并配置 news-skill：
1. 阅读并遵循本 Skill 的 SKILL.md 与 README.md，仓库地址按网络环境二选一：
   国外环境：https://github.com/handaas/news-skill
   国内环境：https://gitee.com/handaas/news-skill
   再按当前环境规范安装到实际包含 SKILL.md 的目录；保留已有配置，不默认全局注册。
2. 使用旷湖SKILL平台在线 MCP：服务地址见本 README「接入 MCP 服务」，平台返回的地址优先。不部署本地 MCP，不索要 INTEGRATOR_ID / SECRET_ID / SECRET_KEY。
3. 将完整 handaas-token 作为环境变量 `NEWS_MCP_TOKEN` 注入运行 Python 的进程，并设置 `NEWS_MCP_URL`；环境变量必须真正到达该进程，只保存文件或在别的终端 export 不算完成。
4. Token 原样保留逗号等字符：不拆分、不解码、不放进 URL、不手动加 Bearer。不要把 Token 写入仓库、SKILL.md、README、示例配置、报告或终端历史；若只能使用仓库外的私有环境文件，权限设为 0600，并由启动器显式加载。不要回显 Token、请求头或原始鉴权错误；Token 注意保管，请勿泄露。
5. 正式执行真实查询：加 --live 生成 HTML / Markdown / JSON 报告；按问题范围使用默认页数预算，不为填模板扩大调用，也不重复请求。生成含图表的 HTML / PDF 报告需要 Node.js（推荐当前 LTS，无需 npm 包）：依赖检查阶段用安全可用的工具检查并在缺失时补齐 Node，不产生付费调用；查看已生成的 HTML 不需要 Node / 服务器 / 网络。真实查询产出的是待 AI 审校草稿，须按 SKILL.md 用 review_report.py 完成实际 AI 审校并生成最终报告后再交付。
6. 只返回脱敏结果：安装目录、私有配置位置、真实查询调用次数与数据范围、报告路径。缺少地址或 Token 时明确说明缺失项，不要把连接失败说成企业没有数据。

handaas-token：<在此粘贴本人在旷湖SKILL平台获取的完整 Token>
```

Token 只放在运行进程的环境变量或仓库外的私有配置中；用户粘贴本指引即视为授权按默认预算执行真实查询；不要擅自扩大调用范围。

### 注册并创建对接器

1. 打开 [旷湖官网](https://www.handaas.com/) 注册并登录。
2. 进入「数据管理」→「对接器」→「创建对接器」。
3. 选择本 Skill 对应的服务类型「企业舆情洞察」。
4. 创建后复制完整 `handaas-token`，按上文注入 `NEWS_MCP_TOKEN`；Token 注意保管，请勿泄露。

### 智能体安装约定

1. 先读 `SKILL.md`，按当前智能体规范安装 Skill 和 Python 依赖；生成含图表的 HTML / PDF 报告另需 Node.js（推荐当前 LTS，无需 npm 包），安装验收时用安全可用的工具检查并在缺失时补齐。以实际包含 `SKILL.md` 的目录为根，不假设另有同名子目录，不默认全局注册，不覆盖已有安装或配置。
2. 使用旷湖SKILL平台为本 Skill 创建的在线 MCP，地址见下文“接入 MCP 服务”，平台地址优先；不克隆上游 MCP 仓库、不启动本地服务，不索要 `INTEGRATOR_ID`、`SECRET_ID` 或 `SECRET_KEY`。
3. 将完整 `handaas-token` 保存到本机智能体私有密钥/环境配置，作为 `NEWS_MCP_TOKEN` 注入运行 Python 脚本的进程，并注入下文对应的 `*_MCP_URL`。脚本不会自动加载 `.env` 或读取 `config.json.remote_mcp`；只注册智能体原生 MCP 也不等于 Python 已配置。宿主无持久环境机制时，可用仓库外私有环境文件由启动器显式加载，POSIX 权限 `0600`、Windows 限当前用户；无法安全持久化时明确报告，不能宣称完成。
4. Token 中的逗号等字符原样保留，不拆分、不解码、不放入 URL、不手动加 Bearer，客户端自行添加 `Authorization: Bearer`。本 Skill 只读取自己的 `NEWS_MCP_TOKEN`，不要配置共享 Token；若已设置其他 Token 变量，先清理以避免混淆。每个 Skill 只使用自己的 `NEWS_MCP_URL`，不能把单一服务地址复用于其他领域。
5. 先用 `mcp_client.get_connection_config()` 确认模式为 `remote`，缺地址时停止，不回退本地；执行 `python scripts/validate_config.py`、`python scripts/mcp_client.py ping` 和 `python scripts/mcp_client.py list-tools`，只做 `initialize/list_tools`，对照 `SKILL.md` 或脚本核对所需工具名。空列表或工具缺失时不得宣称业务可用。默认按下文命令显式 `--dry-run` 生成三件套；若用户的一键指引明确要求正式执行，则完成元数据检查后直接执行真实查询并生成报告。离线生成成功不证明在线权限通过，未验证项必须注明。
6. 只报告安装位置、私有配置文件位置、脱敏连接/工具数及离线报告路径；真实 Token 不进入命令参数、终端历史、日志、回复、仓库、文档、样例配置、报告或共享配置，不输出原始请求头或鉴权错误。普通安装不等于授权真实查询或付费调用。

## 快速验证

不需要配置 MCP 也能先跑起来，验证报告骨架和图表渲染：

```bash
cd news-skill
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# 生成含图表的 HTML 还需要 Node.js（推荐当前 LTS），检查：node --version
python scripts/compose_report.py \
  --enterprise "广州探迹科技有限公司" \
  --dry-run \
  --output output/report.json \
  --report-output output/report.html
```

打开 `output/report.html` 即可看到完整报告效果。生成含图表的 HTML 需要 Node.js（推荐当前 LTS，无需 npm 包，可用 node --version 检查）；打开已生成的 HTML 不需要 Node / 服务器 / 网络，Markdown 输出也不需要 Node。

## 接入 MCP 服务

`news-mcp-server` 是数据接入层。Skill 会自动优先使用 MCP 连接。

### 方式一：使用旷湖SKILL平台在线 MCP（推荐）

在旷湖SKILL平台为本 Skill 一键创建所需在线 MCP 服务，获取平台 Token，并保存为本 Skill 专属的 `NEWS_MCP_TOKEN`。以下沿用仓库现有服务地址，平台返回新地址时以平台为准，不编造平台主页或发现接口。

设置环境变量（token 配置一次即可）：

macOS / Linux：

```bash
export NEWS_MCP_TOKEN="<平台完整Token>"
export NEWS_MCP_URL="https://mcp.handaas.com/news/news_bigdata"
```

Windows PowerShell：

```powershell
$env:NEWS_MCP_TOKEN = "<平台完整Token>"
$env:NEWS_MCP_URL = "https://mcp.handaas.com/news/news_bigdata"
```

`handaas-token` 完整值统一保存为 `NEWS_MCP_TOKEN`，URL 中不重复填写。以上示例只配置当前 shell 及其后续子进程；智能体持久使用须按“智能体安装约定”保存并注入环境，不能仅把 Token 写进文件就宣称生效。

### 方式二：连接本地部署的 MCP 服务

```bash
git clone https://github.com/handaas/handaas-mcp-server
cd handaas-mcp-server/news-mcp-server
python3 -m venv mcp_env
source mcp_env/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env 填入 INTEGRATOR_ID / SECRET_ID / SECRET_KEY
./start_mcp_server.sh
```

在使用 Skill 的 shell 中指定本地 MCP 地址：

macOS / Linux：

```bash
export NEWS_MCP_URL="http://127.0.0.1:8000/mcp"
```

Windows PowerShell：

```powershell
$env:NEWS_MCP_URL = "http://127.0.0.1:8000/mcp"
```

本地 MCP 由你自己的 `.env` 持有凭证，Skill 侧只需要 `NEWS_MCP_URL`。

### MCP 连接验证

```bash
python scripts/mcp_client.py ping
python scripts/mcp_client.py list-tools
```

## 生成报告

### 真实查询 + 渲染

配置好 MCP 连接后，一条命令生成报告：

```bash
python scripts/compose_report.py \
  --enterprise "广州探迹科技有限公司" \
  --output output/report.json \
  --report-output output/report.html \
  --pdf-output output/report.pdf
```

同时产出四个文件：

| 文件 | 说明 |
| --- | --- |
| `output/report.html` | 浏览器直接打开，无需 Node / 服务器 / 网络；图表先显示内嵌静态 SVG，允许 JavaScript 时增强 tooltip / 图例交互 |
| `output/report.md` | Markdown 格式，适合放入 Wiki / 文档系统 |
| `output/report.json` | 结构化原始数据，适合二次处理 |
| `output/report.pdf` | 打印友好版，需安装 Playwright；含图表的生成另需 Node.js |

HTML 打开时先显示生成期内嵌的静态 SVG 图表，无需 JavaScript；允许 JavaScript 时再以 ECharts 增强 tooltip、图例等交互，交互失败仍保留静态图表。静态优先渲染不保证会剥离 SVG / 图片或只展示 HTML 源码的平台能显示图表。

### 重渲染已有 JSON

不重新调用 MCP，直接从已有 JSON 重渲染：

```bash
python scripts/render_report.py \
  --input output/report.json \
  --output output/report.html
```

## 效果预览

> 以下文件来自当前真实验收通过案例；`examples/` 只收录完全通过的公开示例。

| 文件 | 说明 | 链接 |
| --- | --- | --- |
| HTML 报告 | 浏览器直接打开，含离线图表 | [查看](examples/01-reputation-overview/report.html) |
| Markdown 报告 | 纯文本格式，适合 Git / 文档系统 | [查看](examples/01-reputation-overview/report.md) |
| JSON 原始数据 | 结构化数据，适合二次处理 | [查看](examples/01-reputation-overview/report.json) |
| 全部已验收案例 | 本 skill 当前全部完全通过示例 | [查看](examples/README.md) |

完整的 8 场景测试、未完全通过案例和历史运行保存在 [test-records](test-records/README.md)，不作为公开示例。

## 命令行用法

### 配置校验

```bash
python scripts/validate_config.py --allow-placeholders
```

### 模拟运行（不调真实 API）

```bash
python scripts/compose_report.py \
  --enterprise "广州探迹科技有限公司" \
  --dry-run \
  --output output/report.json \
  --report-output output/report.html
```

### 真实查询

```bash
python scripts/compose_report.py \
  --enterprise "广州探迹科技有限公司" \
  --output output/report.json \
  --report-output output/report.html
```

### 关键词模糊补全

输入简称或关键词，自动模糊搜索匹配企业全称：

```bash
python scripts/compose_report.py \
  --enterprise "探迹" \
  --report-output output/report.html
```

## 故障排查

### 1. MCP 客户端依赖缺失

```bash
pip install 'mcp>=1.12.0' httpx
```

### 2. 在线 MCP token 不可用

检查环境变量是否生效：

```bash
echo "$NEWS_MCP_URL"
python scripts/mcp_client.py ping
```

不要把 token 提交到 Git。

### 3. 本地 MCP 连不上

确认服务已启动，Skill 侧指向 `/mcp` 路径：

```bash
curl http://127.0.0.1:8000/mcp
python scripts/mcp_client.py ping
```

### 4. 报告内容为空

使用 `--dry-run` 验证报告骨架是否正常，再逐步排查 MCP 返回数据。

### 5. PDF 导出不可用

PDF 导出需要 Playwright + Chromium；生成含图表的 HTML / PDF 还要求本机 Node.js（推荐当前 LTS，无需 npm 包），缺失时报告生成会明确报错，不会输出空白图表。查看已生成的 HTML 不需要 Node：

```bash
pip install playwright
playwright install chromium
```

## 相关文档

- [SKILL.md](SKILL.md) — Skill 契约（触发描述、意图路由、Golden Path）
- [references/report-output.md](references/report-output.md) — 报告结构规范与数据格式约束
- [references/mcp-tools-reference.md](references/mcp-tools-reference.md) — MCP 工具清单
