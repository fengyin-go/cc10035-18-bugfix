# Web语义抽取与内容理解中心

面向Web结构化数据采集场景，提供页面DOM解析、实体识别、关系抽取、内容分类和知识图谱构建的语义抽取平台

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| DOM解析 | `dom_parser` | 解析配置 | 配置名称、目标URI、解析引擎 |
| 实体抽取 | `entity_extract` | 实体条目 | 实体ID、实体类型、实体名称 |
| 关系图谱 | `relation_graph` | 关系边 | 边ID、源实体、目标实体 |
| 内容分类 | `content_classifier` | 分类标签 | 标签ID、页面URI、一级分类 |
| 模板规则 | `template_rule` | 规则模板 | 模板名称、适用站点、提取字段 |
| 文本分块 | `text_segment` | 文本块 | 块ID、来源页面、采集版本 |
| 文本来源校验数据集 | `text_source_dataset` | 文本块 | 块ID、采集版本、校验结论 |
| 待补采清单 | `backfill_list` | 缺测页 | URL、最近采集状态、上次可用版本 |
| 情感标注 | `sentiment_tag` | 情感记录 | 记录ID、文本片段、情感倾向 |
| Schema对齐 | `schema_align` | 对齐规则 | 规则名称、源Schema、目标Schema |
| 种子调度 | `feed_scheduler` | 调度任务 | 任务名称、URL种子、抓取深度 |
| 质量检查 | `quality_check` | 检查规则 | 规则名称、检查字段、检查类型 |
| 探针选择 | `probe_selector` | 探针配置 | 探针名称、目标页面、选择器列表 |
| 页面快照 | `page_snapshot` | 快照记录 | 快照ID、页面URI、DOM哈希 |
| 抽取指标 | `extract_metric` | 指标记录 | 记录时间、所属任务、成功页面数 |
| 语义结果 | `nlu_result` | 语义记录 | 记录ID、输入文本、意图标签 |
| 知识链接 | `knowledge_link` | 链接记录 | 链接ID、源实体、外部URI |
| 输出目标 | `output_target` | 输出配置 | 配置名称、输出类型、目标地址 |
| 抽取工坊 | `extract_studio` | 交互任务 | 任务名称、示例页面、标注区域 |
| 聚合视图 | `aggregate_view` | 视图配置 | 视图名称、统计维度、聚合字段 |

## 采集链路

文本分块不再按固定模板生成示例块：只把**实际采集到的 HTML** 解析成文档顺序上的
正文片段，没有正文就返回 0 个块。整条链路围绕三个事实展开：

- **采集版本**：同一 URL 每次抓取都是一个版本（`version` 从 1 递增），状态分为
  `有效文本` / `空正文` / `无法获取`。只有有效文本会产生块；空正文和无法获取都会
  进入待补采清单，补采成功后自动解除。
- **文本来源校验数据集**：每个块都能回溯到「采集ID + 采集版本 + 片段序号 + 文本哈希」，
  并带 `已校验 / 历史引用 / 校验失败` 结论。历史示例块仍可解析出处，但不计入有效样本。
- **情感标注**：只消费上述数据集里校验通过的块。分析记录写回待补采清单，带有实际使用的
  采集版本和时效状态；分块清单、数据集、情感统计使用同一个有效样本口径。

关键接口：

| 用途 | 接口 |
| --- | --- |
| 登记一次采集并切分 | `POST /api/capture/pages`、`POST /api/text_segment/segment` |
| 采集中断后重试 | `POST /api/capture/retry` |
| 文本来源校验数据集（可导出） | `GET /api/capture/text-source`、`GET /api/text_segment/dataset` |
| 按页面核对来源与时效 | `GET /api/text_segment/source-check?url=…` |
| 待补采清单 | `GET /api/capture/backfill`、`GET /api/feed_scheduler/backfill` |
| 情感分析（按块/按页） | `POST /api/capture/sentiment/analyze`、`POST /api/sentiment_tag/analyze` |
| 有效样本对账 | `GET /api/capture/consistency`、`GET /api/sentiment_tag/consistency` |
| 交付报告 | `GET /api/capture/report` |

报告会明确交代每个页面采用了哪个采集版本（`capture_version_used` /
`version_source`）、各条分析记录分别用了哪个版本（`analysis_versions_used`），
以及当前空页是否进入有效情感分布（`empty_pages[].included_in_valid_distribution`）。
同一页面最新采集为空、上一次仍有可用文本时，分析沿用最近可用版本并标记
`freshness_conflict`，不会把缺失当成结论。

回归测试：`cd backend && .venv/bin/python -m unittest discover -s tests -v`

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

领域：数据工程
子方向：数据采集、爬虫与RPA
