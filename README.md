# 检验失败组合追因

关联规则的真实业务分析小型项目。完整模拟客户、痛点、上下游、算法公式与一天工作剧本见 [BUSINESS.md](BUSINESS.md)。

## 模块与业务流程
数据接入（JSON/CSV、字段验证、批次指纹）→ 参数化分析（异步 Python worker）→ 结果复核（指标、图形、明细、解释、批准）→ 定时分发（本地收件箱、CSV/JSON、SHA256）；另有业务总览和审计轨迹。
三个贯穿全栈的实体：Dataset 数据批次、Job 分析任务、Report 分发计划；辅助 User、Audit、Delivery。
差分隐私对外发布（`/privacy`）按数据批次共享隐私预算，向合作方只输出加噪统计；涉及 PrivacyBudget、NoiseCache、PrivacyRelease、PrivacyLedger 四个实体。

## 快速启动
```bash
cp .env.example .env
docker compose up -d --build --wait
docker compose exec -T backend python -m scripts.seed_demo
```
访问 http://localhost:18211；后端文档 http://localhost:19211/docs；数据库宿主端口 17711。
`SEED_DEMO=true` 自动幂等 seed；不覆盖用户修改。手动 seed 命令可重复执行。

## 演示账号与权限
|账号|密码|真实权限|
|---|---|---|
|admin@demo.local|Demo123!|接入、执行、批准、分发、暂停、隐私预算设定与发布、读取与下载|
|analyst@demo.local|Demo123!|接入、执行、差分隐私发布、读取与下载；不能批准、安排分发或设定预算|
|viewer@demo.local|Demo123!|内部只读与下载|
|partner@demo.local|Demo123!|合作方：仅能读取和下载已发布的差分隐私统计；原始数据、内部分析、旧归档、预算与流水均 403 拒绝|

## 差分隐私发布（合作方统计）
- **预算按批次共享**：负责人在「隐私发布」页为每个数据批次设定总 ε、总 δ；单次发布 ε∈[0.05,2]，δ=0 走 Laplace 纯 ε-DP，δ∈[1e-6,1e-3] 走 Gaussian (ε,δ)-近似 DP。多次发布按顺序组合累计（总 ε、总 δ 为各次之和）。
- **并发不超支**：发布在同一事务内对批次预算行 `SELECT ... FOR UPDATE` 加锁并校验余额，超额请求返回 409，且不产生发布、缓存或流水残留。
- **噪声只取一次**：以 `(批次, ε, δ)` 为唯一键缓存一整组校准噪声（确定性种子派生）。同批次同隐私参数的后续发布——无论换账号还是调整筛选阈值——都复用同一噪声并零扣费（流水记 `reuse`）；换 ε/δ 才重新取噪并扣费（流水记 `charge`）。
- **同份加噪统计出全部结果**：支持度、置信度、提升度与频繁项集阈值筛选全部由同一份加噪计数推导，发布载荷不含任何真实计数或原始记录。
- **发布与扣减绑定、可恢复**：发布先落 `reserved` 并带 `X-Idempotency-Key` 幂等键；同键重试返回原发布，不重复扣费、不另取噪。进程启动自动恢复遗留的 reserved；恢复时按该噪声是否已被付过费决定补记 charge 或零扣费 reuse，且仅记一次。
- **误差口径**：单计数敏感度为 1；Laplace 尺度 b=1/ε（95% 误差幅度≈3b），Gaussian 尺度≈1.64/ε（95%≈1.96σ）。每次发布在页面与 JSON 中附 `count_error_95`、机制与隐私保证说明。
- **合作方边界**：partner 角色的所有内部路由（datasets/jobs/reports/deliveries/overview/audits/privacy 预算与流水）由 `internal_only` 依赖统一 403；仅 `/privacy/releases`（已发布）与其下载端点可读。

## 技术与本地开发
Python 3.12 / FastAPI / SQLAlchemy / NumPy / SciPy / scikit-learn；MySQL 8.0；Redis 7；Nginx；原生 HTML CSS JavaScript ES modules（用户指定）。后端所有校验、持久化和算法在服务端。无前端打包步骤。
后端：安装 backend/requirements.txt，设置 DATABASE_URL、REDIS_URL、JWT_SECRET、ARTIFACT_DIR 后在 backend/ 执行 `python -m migrations.initial`、`python -m scripts.seed_demo`、`uvicorn src.main:app`，另开 `python -m src.worker`。前端使用带 /api 反向代理的 Nginx，直接打开 index.html 不支持 API。

## 目录与工程约定
backend/src/{models,schemas,services,routes,controllers,middlewares,constants,config,utils}；backend/scripts/seed_demo.py；backend/migrations/initial.py。
frontend/src/{api,stores,types,components/common,hooks,pages,router,utils,constants}。
Role 枚举：admin/analyst/viewer/partner；JobStatus：queued/running/completed/failed；ReleaseStatus：reserved/published/failed。后端 backend/src/constants/enums.py，前端 frontend/src/constants/enums.js；路由权限在 middlewares/auth.py（`internal_only` 拦截合作方、`admin_only` 限定负责人）；页面按相同角色控制操作入口。

## 环境变量与 Docker
COMPOSE_PROJECT_NAME=assay-rules；FRONTEND_PORT=18211；BACKEND_PORT=19211；DB_PORT=17711；DB_PASSWORD、JWT_SECRET 见 .env.example；SEED_DEMO 默认 true。共享命名产物卷供后端下载与 worker 写入，数据库/Redis 各持久卷。
恰好五服务均有健康检查。Redis 队列加 SQL 排队状态兜底；worker 按 next_run 生成本地收件箱文件。周期 60 秒至 30 天。分发结果为批准分析的固定快照；若需新数据重新计算并建立新计划。当前不连接邮件或企业聊天。
错误响应、请求编号、Redis 每 IP 每分钟 240 次限流、JWT 和数据库操作审计。演示密钥与密码仅供本机模拟；生产部署前配置独立密钥与账号。

## 数据与验收
所有数据为固定随机种子生成的模拟业务数据，不能作为实际生产决策证据。项目独立算法、输入边界及业务局限详见 BUSINESS.md。测试与截图统一存放 ../../test-artifacts/gb-1012/，源码不含日志/截图/测试报告。
差分隐私能力随仓库附带 5 个无外部服务依赖的自验证脚本（根目录，sqlite + 内存桩，可直接运行）：`verify_dp.py`（取噪确定性、复用、扣减、幂等、中断恢复，50 项）、`verify_dp_api.py`（HTTP 越权拦截与完整发布流，36 项）、`verify_dp_concurrency.py`（预算竞争不超支，9 项）、`verify_dp_semantics.py`（相邻数据集不可区分与噪声分布，12 项）、`verify_frontend.mjs`（隐私页与合作方页渲染，12 项）。
清理本项目演示数据：`docker compose down -v --remove-orphans`（会删除本项目命名卷）。

## License
MIT，见 LICENSE。
