# 数学教学 Agent Demo

当前已完成 M0“有理数题库准备”、M1“工程骨架与基础数据”、M2“学生画像与规则推荐基础”和 M3“个性化组卷、教师调整与发布”。系统可将历史作答重放为画像，并为多名学生生成可解释、可调整、发布后冻结的独立试卷。

## 当前功能

- FastAPI + SQLAlchemy + PostgreSQL 后端；
- Alembic 十段迁移、推荐审计、发布试卷快照保护和版本生命周期管理；
- 139 组完整题库、初始 41 组自动组卷题、知识树和 5 名虚拟学生的幂等导入；
- 3 次历史作业、15 份发布试卷、150 条确认作答及修订记录；
- 题库列表、筛选、分页、详情、教师录题和业务删除 API / 页面；
- 选择题、填空题支持教师修改答案、保存审核草稿、确认入池、撤回后重新编辑和版本冲突保护；
- 填空题支持普通文本、数值和可视化公式输入；公式以 LaTeX 源码保存，不保存为图片；
- 学生列表、详情、历史作答、新增和业务删除 API / 页面；
- 从 0.5 开始按 `(answered_at, id)` 重放 EMA，生成 50 条学生 × 考点画像汇总；
- 画像默认聚焦最多 5 个薄弱、近期错误或低证据考点，用一张联动柱状图和一张复用趋势图解释诊断；完整考点、逐次作答和最近错误按需展开；
- 每个画像点和推荐理由可回溯到历史 Attempt、修订号与发布试卷快照；
- 教师可设置新知识 / 回顾范围、题量、题型、难度、近期去重和考点占比，预览规则推荐及候选缺口；
- 564 个唯一公式/图片资源转换为浏览器可显示格式；
- 多学生只读预检与原子生成，每名学生拥有独立 draft PaperInstance；
- 草稿支持规则一致的备选换题、删题、连续调序和乐观锁冲突保护；
- 单份 / 批量发布、全局唯一实例码、按码精确查询和发布后复制新版本；
- 推荐参数、分项得分、证据、候选缺口与教师编辑事件均随试卷保存；
- M3 自动验收、52 项后端测试、前端 production build 和桌面 / 390px 浏览器验收。

推荐预览仍保持只读；只有教师确认生成后才创建 Assignment 和草稿。在线判分和改判事务在 M4 完成；打印答题区和图片识别在 M6 完成。

## 目录

```text
code/
├── backend/                       FastAPI、模型、迁移、种子、验收和测试
├── frontend/                      React + TypeScript + Ant Design
├── data/
│   ├── seed/                      M0 正式种子
│   ├── assets/rational/           M0 原始资源
│   └── web/rational/              M1 Web 资源与 manifest
├── scripts/
│   ├── build_rational_m0.py
│   ├── validate_m0.py
│   ├── convert_seed_assets.py
│   └── convert_wmf_to_png.ps1
├── docker-compose.yml
└── .env.example
```

## Docker Compose 启动

### 首次运行

只在第一次创建容器、镜像和数据库卷时执行：

```powershell
cd "D:\desktop\math-agent\code"
Copy-Item .env.example .env
docker compose up -d --build
docker compose exec backend python -m scripts.seed_database
docker compose exec backend python -m scripts.rebuild_mastery --all
docker compose exec backend python -m scripts.verify_m1
docker compose exec backend python -m scripts.verify_m2
docker compose exec backend python -m scripts.verify_m3
```

`seed_database` 和 `rebuild_mastery` 是初始化业务数据，不需要在日常启动时重复执行。后端容器每次启动都会自动执行 `alembic upgrade head`。

### 非首次运行（日常启动）

以后正常启动只需要：

```powershell
cd "D:\desktop\math-agent\code"
docker compose up -d
docker compose ps
```

`docker compose up -d` 会复用已有镜像、容器配置和 PostgreSQL 数据卷，不会重新导入种子，也不会清空已创建的学生画像和作业。

查看后端、前端实时日志：

```powershell
docker compose logs -f backend frontend
```

按 `Ctrl+C` 只退出日志查看，不会停止容器。

如果之前使用的是 `docker compose stop`，也可以快速恢复原容器：

```powershell
docker compose start
docker compose ps
```

### 代码或依赖发生变化时

当前源码通过目录挂载进入容器，普通 Python / TypeScript 源码修改会由后端 reload 和 Vite 自动加载，通常不需要重新构建镜像。

修改了 `backend/pyproject.toml`、`frontend/package.json` 或 Dockerfile 后再执行：

```powershell
cd "D:\desktop\math-agent\code"
docker compose build backend frontend
docker compose run --rm frontend npm install
docker compose up -d
```

拉取到新的 Alembic 迁移时不需要手动执行迁移；启动 backend 时会自动升级到最新版本。可以通过以下命令确认：

```powershell
docker compose exec backend python -m alembic current
docker compose exec backend python -m scripts.verify_m3
```

访问：

```text
前端：http://localhost:5173
后端文档：http://localhost:8000/docs
健康检查：http://localhost:8000/api/v1/health
就绪检查：http://localhost:8000/api/v1/ready
```

临时停止但保留容器：

```powershell
cd "D:\desktop\math-agent\code"
docker compose stop
```

关闭并移除本项目的容器和网络：

```powershell
cd "D:\desktop\math-agent\code"
docker compose down
```

该命令会保留 PostgreSQL 数据卷，下次启动时数据仍然存在。不要添加 `-v`，否则会同时删除数据库卷。

Compose 启动后端时会执行 `alembic upgrade head`，但不会自动导入业务种子。种子导入使用显式命令，避免每次启动静默修改数据。

如果构建时报 `failed to fetch oauth token` 或 `auth.docker.io:443` 超时，说明故障发生在 Docker Hub 网络连接，不是项目代码。可以分别重试基础镜像下载，再重新构建：

```powershell
docker pull node:22-alpine
docker pull python:3.12-slim
docker pull postgres:16-alpine
docker compose build backend frontend
```

前端容器内安装依赖默认使用 `https://registry.npmmirror.com`；如需改回 npm 官方源，可执行：

```powershell
docker compose build --build-arg NPM_REGISTRY=https://registry.npmjs.org frontend
```

如果 Docker Hub 仍不可达，可直接使用下面的 D 盘本地环境方式。

## 当前电脑直接启动（推荐）

当前电脑已经完成 conda 环境创建、依赖安装、数据库初始化和种子数据导入。以后运行项目时，**不需要再次执行 `conda create`、`initdb`、`createdb`、数据库迁移或种子导入**。

“不创建默认 C 盘 conda 环境”的意思不是不用 conda，而是使用 `--prefix` 把 conda 环境放在本项目的 `code/.conda-env/` 中。运行时直接调用其中的 Python，不需要执行 `conda activate`。环境、包缓存和 PostgreSQL 数据都保存在 D 盘项目目录内。

每次启动需要打开三个 PowerShell 窗口。

### 1. 启动数据库

在第一个 PowerShell 窗口运行：

```powershell
cd "D:\desktop\math-agent\code"

& "D:\desktop\math-agent\code\.conda-env\Library\bin\pg_ctl.exe" `
  -D "D:\desktop\math-agent\code\.postgres-data" `
  -l "D:\desktop\math-agent\code\.postgres-data\server.log" `
  -o "-p 55432" start
```

项目现在位于全英文路径 `D:\desktop\math-agent\code`，PostgreSQL 可以直接使用项目内环境和数据目录，不再需要额外的目录联接。看到 `server started` 表示数据库已经在后台运行，这个窗口可以关闭。

### 2. 启动后端

在第二个 PowerShell 窗口运行：

```powershell
cd "D:\desktop\math-agent\code\backend"
$env:DATABASE_URL = "postgresql+psycopg://postgres@localhost:55432/math_agent"
$env:WEB_ASSET_DIR = "D:\desktop\math-agent\code\data\web\rational"
& "D:\desktop\math-agent\code\.conda-env\python.exe" -m uvicorn app.main:app --reload --port 8000
```

启动后可访问后端接口文档：<http://localhost:8000/docs>。

### 3. 启动前端

在第三个 PowerShell 窗口运行：

```powershell
cd "D:\desktop\math-agent\code\frontend"
npm run dev
```

启动后访问：<http://localhost:5173>。

### 停止项目

先在前端和后端窗口分别按 `Ctrl+C`，再运行：

```powershell
& "D:\desktop\math-agent\code\.conda-env\Library\bin\pg_ctl.exe" `
  -D "D:\desktop\math-agent\code\.postgres-data" stop
```

## 首次创建 D 盘 conda 环境（当前电脑无需再执行）

以下命令只用于在新电脑或删除 `.conda-env` 后重新安装。`--prefix "$PWD\.conda-env"` 会创建 conda 环境，但把它放在当前 D 盘项目目录，而不是 conda 默认的 C 盘环境目录：

```powershell
cd "D:\desktop\math-agent\code"
$env:CONDA_PKGS_DIRS = "$PWD\.conda-pkgs"
conda create --prefix "$PWD\.conda-env" -c conda-forge python=3.12 postgresql=16 -y

$env:PIP_CACHE_DIR = "$PWD\.pip-cache"
& "$PWD\.conda-env\python.exe" -m pip install -e ".\backend[dev]" `
  -i https://pypi.tuna.tsinghua.edu.cn/simple
```

项目根目录为全英文路径，可以直接初始化项目内 PostgreSQL 数据目录：

```powershell
& "D:\desktop\math-agent\code\.conda-env\Library\bin\initdb.exe" `
  -D "D:\desktop\math-agent\code\.postgres-data" `
  -U postgres -A trust --encoding=UTF8 --locale=C

& "D:\desktop\math-agent\code\.conda-env\Library\bin\pg_ctl.exe" `
  -D "D:\desktop\math-agent\code\.postgres-data" `
  -l "D:\desktop\math-agent\code\.postgres-data\server.log" `
  -o "-p 55432" start

& "D:\desktop\math-agent\code\.conda-env\Library\bin\createdb.exe" `
  -h localhost -p 55432 -U postgres math_agent
```

迁移和导入：

```powershell
$env:DATABASE_URL = "postgresql+psycopg://postgres@localhost:55432/math_agent"
cd backend
& "..\.conda-env\python.exe" -m alembic upgrade head
& "..\.conda-env\python.exe" -m scripts.seed_database
& "..\.conda-env\python.exe" -m scripts.rebuild_mastery --all
& "..\.conda-env\python.exe" -m scripts.verify_m2
& "..\.conda-env\python.exe" -m scripts.verify_m3
```

本地启动后端：

```powershell
$env:DATABASE_URL = "postgresql+psycopg://postgres@localhost:55432/math_agent"
cd backend
& "..\.conda-env\python.exe" -m uvicorn app.main:app --reload --port 8000
```

另开终端启动前端：

```powershell
cd "D:\desktop\math-agent\code\frontend"
npm install
npm run dev
```

停止项目内 PostgreSQL：

```powershell
& "D:\desktop\math-agent\code\.conda-env\Library\bin\pg_ctl.exe" `
  -D "D:\desktop\math-agent\code\.postgres-data" stop
```

## 资源转换

已生成的 Web 资源位于 `data/web/rational/`。重新构建 M0 数据后执行：

```powershell
python .\scripts\convert_seed_assets.py
```

脚本只处理正式种子题实际引用的资源，并生成 `asset_manifest.json`。Windows 优先使用 ImageMagick，未安装时使用项目内 PowerShell + GDI+ 转换器。转换失败会返回非零状态，不会覆盖原始 WMF。

## 测试与验收

后端：

```powershell
$env:DATABASE_URL = "postgresql+psycopg://postgres@localhost:55432/math_agent"
cd code\backend
& "..\.conda-env\python.exe" -m pytest -q
& "..\.conda-env\python.exe" -m scripts.verify_m1
& "..\.conda-env\python.exe" -m scripts.verify_m2
```

前端：

```powershell
cd code\frontend
npm run build
npm run test:e2e
```

M0 回归：

```powershell
cd code
python .\scripts\validate_m0.py
```

## 种子导入规则

稳定身份键：

- `KnowledgePoint.code`；
- `ExamPoint.code`；
- `Question.original_qid`；
- `Student.student_code`；
- `Assignment.seed_key`；
- `PaperInstance.instance_code`。

第二次执行 `seed_database` 不会生成重复题目、学生、作业、试卷或作答。题目的 `correct_answer`、多空 `items`、归一化规则和资源列表均按 JSONB 原样保存。

虚拟学生历史完整经过：

```text
Assignment
→ published PaperInstance
→ PaperInstanceItem 快照
→ manual Submission
→ teacher_confirmed Attempt
→ AttemptRevision
```

数据库触发器拒绝修改或删除已发布试卷，也拒绝增删改其中的题目快照。

## 题库分层结果

- 完整题库：139 组，全部可在题库页面查看；
- 初始自动组卷池：41 组，其中填空 31、选择 10；教师确认或撤回后数量会动态变化；
- 初始待入库验证：98 组，其中优先处理选择题和填空题，计算题与解答题保留原题和参考答案供老师核验；
- 自动组卷池覆盖 21 个主考点、4 档难度；
- 41 组自动组卷题全部为 `machine_ready`、`source_review_status=reviewed`；
- 自动校验错误：0。

全量数据来自 `data/interim/rational_all.json`，其中 `machine_ready` 题可参加推荐组卷，`raw_answer_only` 题只进入展示层。知识点与考点使用校本资料中的出现顺序，不再按名称排序。

待入库的选择题和填空题可从题目详情页点击“审核答案并进入组卷池”。老师可先保存审核草稿，再执行“校验并确认入池”。已入池题目可执行“撤回并重新编辑”，撤回后不再参与新组卷，修改并重新校验后可再次入池。每次保存、入池和撤回都会记录操作人、版本和前后答案。数值只接收整数或小数，分数、根式等请选择公式输入。当前公式采用 LaTeX 精确匹配，暂不承诺不同代数写法的等价判定。

已经保存过教师审核记录的答案属于业务数据；以后再次执行 `seed_database` 时会保留其答案、判分规则、审核版本和入池状态。

## M2 画像重算

`StudentMastery` 是可重建汇总，Attempt、AttemptRevision 和已发布试卷快照才是证据来源。重算一个或全部学生：

```powershell
cd code\backend
$env:DATABASE_URL = "postgresql+psycopg://postgres@localhost:55432/math_agent"
& "..\.conda-env\python.exe" -m scripts.rebuild_mastery --student-code S001 --dry-run
& "..\.conda-env\python.exe" -m scripts.rebuild_mastery --all
```

查询画像和推荐预览不会写入数据库。改判、撤销确认和新提交接入后，M4 应在同一事务中复用现有重放与汇总写入服务。

## M3 组卷与发布

教师从“组卷”页面选择学生、教学范围和题量，先进行不会写库的全班预检，再原子生成草稿。草稿可换题、删题、调序或整份删除；发布时生成 `RAT-XXXX-XXXX` 实例码并冻结快照。已发布版本可以撤回并重新发布，但不能物理删除；修订版发布时自动将直接前版标记为“已被更新”。旧编号始终定位旧版，同时返回其有效、撤回或已被更新状态。

M3 只读数据验收：

```powershell
cd code\backend
$env:DATABASE_URL = 'postgresql+psycopg://postgres@localhost:55432/math_agent'
& '..\.conda-env\python.exe' -m scripts.verify_m3
```

教师可从题库页面手动录入新题，新题先进入待审核区，选择题和填空题完成结构化答案审核后可进入自动组卷池。题目与学生的“删除”采用业务归档：从当前列表、推荐和后续组卷中移除，但保留既有试卷快照、作答和审计证据。

## 下一阶段

M4 基于明确的已发布 `paper_instance_id` 和其中的答案 / 判分规则快照，实现手工答案录入、确定性判分、人工复核、AttemptRevision 及画像事务重算。不得回读当前 Question 的答案，也不得将旧实例码重定向到最新版。
