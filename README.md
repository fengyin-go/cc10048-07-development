# 多维指标分析与实验评估平台

面向业务指标口径管理、多维分析查询、AB实验评估、分群圈选和数据服务发布的分析应用平台

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

健康检查：`curl http://127.0.0.1:8012/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5176/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8012`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 指标口径 | `metric_def` | 指标定义 | 指标标识、指标名称、计算公式 |
| 多维查询 | `dim_query` | 查询方案 | 方案标识、查询指标、维度行 |
| 实验管理 | `experiment` | 实验方案 | 方案标识、实验名称、对照组 |
| 显著性检验 | `significance` | 检验任务 | 任务标识、关联实验、检验方法 |
| 人群圈选 | `cohort` | 人群分群 | 分群标识、分群名称、圈选条件 |
| 数据服务 | `data_service` | 服务接口 | 接口标识、关联指标、接口路径 |
| 异常检测 | `anomaly_detect` | 检测规则 | 规则标识、关联指标、检测算法 |
| 分析图表 | `analysis_chart` | 图表配置 | 图表标识、数据来源、图表类型 |
| 预聚合任务 | `aggregate_job` | 聚合任务 | 任务标识、目标指标、聚合维度 |
| 下钻分析 | `drill_down` | 下钻路径 | 路径标识、起始指标、下钻维度 |
| 数据导出 | `data_export` | 导出任务 | 任务标识、查询方案、导出格式 |
| 数据时效 | `data_freshness` | 时效检查 | 检查标识、数据来源、期望时效 |
| 查询缓存 | `query_cache` | 缓存策略 | 策略标识、关联查询、缓存键 |
| 分群对比 | `segment_compare` | 对比方案 | 方案标识、分群A、分群B |
| 指标校准 | `metric_calibrate` | 校准记录 | 记录标识、校准指标、原口径值 |
| 查询日志 | `query_log` | 查询记录 | 记录标识、查询内容、查询用户 |
| 数据采样 | `data_sample` | 采样配置 | 配置标识、数据来源、采样方法 |
| 报告调度 | `report_schedule` | 报告任务 | 任务标识、报告内容、接收人 |

## 已发表结果筛选与复算受理

多维查询与预聚合任务工作台内嵌 `frontend/src/views/PublishedQueryPanel.vue`，
后端领域逻辑在 `backend/app/services/published_result.py`，路由挂在 `/api/domain` 下。

- `GET /api/domain/published_result/meta`：可选指标版本、来源范围、分区日期和分区地区。
- `GET /api/domain/published_result/facts`：按 `指标名称` + `实际指标版本`（或 `版本口径=历史/实际`）
  + `分区日期` + `来源范围` 精确查询已发表事实；版本不做就近取最新，来源范围不做子串匹配。
- `GET /api/domain/published_result/dimensions`：维度必须带完整指标口径，同名维度不会跨指标混入。
- `POST /api/domain/published_result/filter`：生成一轮可接续筛选，返回接续令牌、事实快照和证据快照；
  带 `接续令牌` 再次调用即回看同一轮快照，证据被更正后仍保留变更前结果。
- `GET /api/domain/published_result/rounds/{轮次号}` 与 `/evidence`：按轮次读取结果和证据修订前后。
- `POST /api/domain/published_result/rounds/{轮次号}/recompute`：按分区提交复算，
  同一筛选同一合格分区只生成一次有效受理；暂缓、待核或证据更正的分区进入待核查并给出后续核对。
- `POST /api/domain/aggregate_job/{任务号}/accept-recompute`：预聚合任务用接续令牌关联实际数据源、
  领取地区数据，返回实际重算范围与待核查范围。
- 证据更正只追加修订（`POST .../evidence/{证据标识}/correct`），补交核验
  （`POST .../evidence/{证据标识}/submit`）不改写历史已发表度量。

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

领域：数据工程
子方向：数据分析与数据应用
