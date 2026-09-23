# 数学教学 Agent Demo

当前已完成 M0“有理数题库准备”、M1“工程骨架与基础数据”和 M2“学生画像与规则推荐基础”。系统将 150 条确认作答按历史试卷快照重放为可解释画像，并在教师指定范围内提供可追溯的规则推荐预览。

## 当前功能

- FastAPI + SQLAlchemy + PostgreSQL 后端；
- Alembic 四段迁移及发布试卷快照保护；
- 30 组种子题、知识树和 5 名虚拟学生的幂等导入；
- 3 次历史作业、15 份发布试卷、150 条确认作答及修订记录；
- 题库列表、筛选、分页和详情 API / 页面；
- 学生列表、详情和历史作答 API / 页面；
- 从 0.5 开始按 `(answered_at, id)` 重放 EMA，生成 50 条学生 × 考点画像汇总；
- 画像默认聚焦最多 5 个薄弱、近期错误或低证据考点，用一张联动柱状图和一张复用趋势图解释诊断；完整考点、逐次作答和最近错误按需展开；
- 每个画像点和推荐理由可回溯到历史 Attempt、修订号与发布试卷快照；
- 教师可设置新知识 / 回顾范围、题量、题型、难度、近期去重和考点占比，预览规则推荐及候选缺口；
- 112 个 WMF 公式资源转换为浏览器可显示的 PNG；
- M2 自动验收、39 项后端测试、前端 production build 和桌面 / 窄屏浏览器验收。

M2 的推荐只生成只读预览，不创建 Assignment 或 PaperInstance。正式组卷、教师调整与发布在 M3 完成；在线判分和改判事务在 M4 完成；图片识别在 M6 完成。

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

首次运行：

```powershell
cd code
Copy-Item .env.example .env
docker compose build
docker compose up -d postgres backend frontend
docker compose exec backend python -m scripts.seed_database
docker compose exec backend python -m scripts.rebuild_mastery --all
docker compose exec backend python -m scripts.verify_m2
```

访问：

```text
前端：http://localhost:5173
后端文档：http://localhost:8000/docs
健康检查：http://localhost:8000/api/v1/health
就绪检查：http://localhost:8000/api/v1/ready
```

关闭本项目的全部 Docker 容器和网络：

```powershell
cd "D:\desktop\数学教学知识库与agent\code"
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
cd "D:\desktop\数学教学知识库与agent\code"

if (-not (Test-Path "D:\desktop\math-agent-code")) {
    New-Item -ItemType Junction -Path "D:\desktop\math-agent-code" -Target (Get-Location)
}

& "D:\desktop\math-agent-code\.conda-env\Library\bin\pg_ctl.exe" `
  -D "D:\desktop\math-agent-code\.postgres-data" `
  -l "D:\desktop\math-agent-code\.postgres-data\server.log" `
  -o "-p 55432" start
```

`math-agent-code` 是指向当前项目的目录联接，用来避开 PostgreSQL 对中文路径的兼容问题，不会复制项目或额外保存一份数据。看到 `server started` 表示数据库已经在后台运行，这个窗口可以关闭。

### 2. 启动后端

在第二个 PowerShell 窗口运行：

```powershell
cd "D:\desktop\math-agent-code\backend"
$env:DATABASE_URL = "postgresql+psycopg://postgres@localhost:55432/math_agent"
$env:WEB_ASSET_DIR = "D:\desktop\math-agent-code\data\web\rational"
& "D:\desktop\math-agent-code\.conda-env\python.exe" -m uvicorn app.main:app --reload --port 8000
```

启动后可访问后端接口文档：<http://localhost:8000/docs>。

### 3. 启动前端

在第三个 PowerShell 窗口运行：

```powershell
cd "D:\desktop\数学教学知识库与agent\code\frontend"
npm run dev
```

启动后访问：<http://localhost:5173>。

### 停止项目

先在前端和后端窗口分别按 `Ctrl+C`，再运行：

```powershell
& "D:\desktop\math-agent-code\.conda-env\Library\bin\pg_ctl.exe" `
  -D "D:\desktop\math-agent-code\.postgres-data" stop
```

## 首次创建 D 盘 conda 环境（当前电脑无需再执行）

以下命令只用于在新电脑或删除 `.conda-env` 后重新安装。`--prefix "$PWD\.conda-env"` 会创建 conda 环境，但把它放在当前 D 盘项目目录，而不是 conda 默认的 C 盘环境目录：

```powershell
cd code
$env:CONDA_PKGS_DIRS = "$PWD\.conda-pkgs"
conda create --prefix "$PWD\.conda-env" -c conda-forge python=3.12 postgresql=16 -y

$env:PIP_CACHE_DIR = "$PWD\.pip-cache"
& "$PWD\.conda-env\python.exe" -m pip install -e ".\backend[dev]" `
  -i https://pypi.tuna.tsinghua.edu.cn/simple
```

Windows PostgreSQL 在中文路径执行 `initdb` 时可能发生编码错误。可创建只包含英文的目录联接，实际文件仍保存在项目中：

```powershell
New-Item -ItemType Junction -Path "D:\desktop\math-agent-code" -Target "$PWD"

& "D:\desktop\math-agent-code\.conda-env\Library\bin\initdb.exe" `
  -D "D:\desktop\math-agent-code\.postgres-data" `
  -U postgres -A trust --encoding=UTF8 --locale=C

& "D:\desktop\math-agent-code\.conda-env\Library\bin\pg_ctl.exe" `
  -D "D:\desktop\math-agent-code\.postgres-data" `
  -l "D:\desktop\math-agent-code\.postgres-data\server.log" `
  -o "-p 55432" start

& "D:\desktop\math-agent-code\.conda-env\Library\bin\createdb.exe" `
  -h localhost -p 55432 -U postgres math_agent
```

迁移和导入：

```powershell
$env:DATABASE_URL = "postgresql+psycopg://postgres@localhost:55432/math_agent"
cd backend
& "..\.conda-env\Scripts\alembic.exe" upgrade head
& "..\.conda-env\python.exe" -m scripts.seed_database
& "..\.conda-env\python.exe" -m scripts.rebuild_mastery --all
& "..\.conda-env\python.exe" -m scripts.verify_m2
```

本地启动后端：

```powershell
$env:DATABASE_URL = "postgresql+psycopg://postgres@localhost:55432/math_agent"
cd backend
& "..\.conda-env\python.exe" -m uvicorn app.main:app --reload --port 8000
```

另开终端启动前端：

```powershell
cd code\frontend
npm install
npm run dev
```

停止项目内 PostgreSQL：

```powershell
& "D:\desktop\math-agent-code\.conda-env\Library\bin\pg_ctl.exe" `
  -D "D:\desktop\math-agent-code\.postgres-data" stop
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

## M0 数据结果

- 全量抽取：139 组；
- 正式种子：30 组，其中填空 20、选择 10；
- 覆盖 10 个主考点、4 档难度；
- 30 组全部 `machine_ready`、`source_review_status=reviewed`；
- 自动校验错误：0。

全量中间库仍保留在 `data/interim/rational_all.json`。M1 只把 `data/seed/rational_questions.json` 和 `data/seed/rational_knowledge_tree.json` 导入业务数据库。

## M2 画像重算

`StudentMastery` 是可重建汇总，Attempt、AttemptRevision 和已发布试卷快照才是证据来源。重算一个或全部学生：

```powershell
cd code\backend
$env:DATABASE_URL = "postgresql+psycopg://postgres@localhost:55432/math_agent"
& "..\.conda-env\python.exe" -m scripts.rebuild_mastery --student-code S001 --dry-run
& "..\.conda-env\python.exe" -m scripts.rebuild_mastery --all
```

查询画像和推荐预览不会写入数据库。改判、撤销确认和新提交接入后，M4 应在同一事务中复用现有重放与汇总写入服务。

## 下一阶段

M3 复用 M2 的画像和推荐服务创建每名学生的草稿试卷，完成教师换题、删题、调序、发布和快照冻结。推荐参数、分项得分、证据引用与候选缺口应继续保留，不能在组卷路由中复制另一套推荐算法。
