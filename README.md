# 数学教学 Agent Demo

当前已完成 M0“有理数题库准备”和 M1“工程骨架与基础数据”。系统使用 30 组已审核、可确定性判分的有理数选择 / 填空题，提供题库查询、资源展示、虚拟学生和可追溯历史作答，为 M2 学生画像计算提供输入。

## 当前功能

- FastAPI + SQLAlchemy + PostgreSQL 后端；
- Alembic 四段迁移及发布试卷快照保护；
- 30 组种子题、知识树和 5 名虚拟学生的幂等导入；
- 3 次历史作业、15 份发布试卷、150 条确认作答及修订记录；
- 题库列表、筛选、分页和详情 API / 页面；
- 学生列表、详情和历史作答 API / 页面；
- 112 个 WMF 公式资源转换为浏览器可显示的 PNG；
- M1 自动验收、API 测试和前端 production build。

M1 不计算学生掌握度，不实现个性化组卷、在线判分或 OCR。这些功能分别在 M2–M4、M6 实现。

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
docker compose exec backend python -m scripts.verify_m1
```

访问：

```text
前端：http://localhost:5173
后端文档：http://localhost:8000/docs
健康检查：http://localhost:8000/api/v1/health
就绪检查：http://localhost:8000/api/v1/ready
```

Compose 启动后端时会执行 `alembic upgrade head`，但不会自动导入业务种子。种子导入使用显式命令，避免每次启动静默修改数据。

如果 Docker Hub 暂时不可达，可使用下面的 D 盘本地环境方式。

## 项目内 conda 环境启动

以下路径均位于当前 `code/` 目录，不创建默认 C 盘 conda 环境：

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
& "..\.conda-env\python.exe" -m scripts.verify_m1
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
```

前端：

```powershell
cd code\frontend
npm run build
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

## 下一阶段

M2 从当前 `teacher_confirmed` Attempt 按时间顺序重放，生成 `StudentMastery`、证据量、考点掌握度和初步弱点。不得直接手写 mastery 代替历史作答计算。
