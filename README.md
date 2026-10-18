# 数学教学 Agent Demo

当前已完成 M0～M5，并实现 M6“固定答题区与 VLM 图片识别”的工程闭环。系统可以打印不含标答的学生试卷，上传答题区照片，由视觉模型只抄读答案，再经教师核对、确定性规则判分、修订审计和画像重算进入下一轮推荐。真实 VLM 的质量验收仍需配置供应商凭据并使用真实样本集运行评估脚本，不能用内置 fake adapter 代替。

## 当前功能

- FastAPI + SQLAlchemy + PostgreSQL 后端；
- Alembic 十三段迁移、推荐审计、发布试卷快照保护、版本生命周期、批改审计和图片识别守卫；
- 139 组完整题库、初始 45 组自动组卷题、知识树和 5 名虚拟学生的幂等导入；
- 3 次历史作业、15 份发布试卷、150 条确认作答及修订记录；
- 题库列表、筛选、分页、详情、教师录题和业务删除 API / 页面；
- 选择题、填空题支持教师修改答案、保存审核草稿、确认入池、撤回后重新编辑和版本冲突保护；
- 填空题支持普通文本、数值和可视化公式输入；公式区内置分数、根号、幂、下标等常用按钮以及完整公式键盘，表达式以 ASCII Math 或 LaTeX 文本保存，不保存为图片；
- 学生列表、详情、历史作答、新增和业务删除 API / 页面；
- 从 0.5 开始按 `(answered_at, id)` 重放 EMA，生成 50 条学生 × 知识点画像汇总；
- 画像默认聚焦最多 5 个薄弱、近期错误或低证据知识点，用一张联动柱状图和一张复用趋势图解释诊断；完整知识点、逐次作答和最近错误按需展开；
- 每个画像点和推荐理由可回溯到历史 Attempt、修订号与发布试卷快照；
- 教师可设置新知识 / 回顾范围、题量、题型、难度、近期去重和单知识点占比，预览规则推荐及候选缺口；
- 564 个唯一公式/图片资源转换为浏览器可显示格式；
- 多学生只读预检与原子生成，每名学生拥有独立 draft PaperInstance；
- 草稿支持规则一致的备选换题、删题、连续调序和乐观锁冲突保护；
- 单份 / 批量发布、全局唯一实例码、按码精确查询和发布后复制新版本；
- 推荐参数、分项得分、证据、候选缺口与教师编辑事件均随试卷保存；
- 手动录入和修正支持选择题、精确文本、可视化公式、受限数值表达式和小题组，无法可靠判断时进入人工复核；
- 一份试卷一个 Submission、一题一个 Attempt，重复提交不重复计数，改判追加 AttemptRevision；
- 教师确认、修正答案或撤销确认后，在同一事务中按当前证据全量重算学生画像；
- 批改工作台、逐题结果、修订时间线和画像入口支持桌面与 390px 页面；
- 班级学情总览按发布快照和当前有效修订聚合，支持知识点正确率、掌握度参考均值、证据覆盖、未评估状态和学生下钻；
- 作业进度只统计每名学生的当前有效发布版本，区分未提交、待复核、已完成、草稿和历史作废版本；
- `m5_start` / `m5_showcase` 两套固定演示场景、隔离数据库安全重置脚本和只读 `verify_m5.py`；
- 页面级懒加载，ECharts 与 zrender 独立分包；学情页和批改筛选 URL 支持桌面与 390px 页面；
- M1～M6 只读验收、97 项后端测试、前端 production build，以及桌面 / 390px 浏览器回归。

推荐预览仍保持只读；只有教师确认生成后才创建 Assignment 和草稿。M4 首次录入会锁定题库中当时已发布且可机判的标准答案：录入前完成的答案修订会直接用于首次评分；只有提交之后题库再次修改，已有提交才显示“重新评分”。重新评分由教师显式触发并追加修订，始终不改写 `PaperInstanceItem` 发布快照。M6 的模型置信度仅用于展示，所有图片作答初始均为 `needs_review`，教师确认前不会更新画像。

## 目录

```text
code/
├── backend/                       FastAPI、模型、迁移、种子、验收和测试
├── frontend/                      React + TypeScript + Ant Design
├── data/
│   ├── seed/                      M0 正式种子
│   │   └── m5_demo_scenarios.json M5 固定演示场景合同
│   ├── assets/rational/           M0 原始资源
│   └── web/rational/              M1 Web 资源与 manifest
├── scripts/
│   ├── build_rational_m0.py
│   ├── validate_m0.py
│   ├── convert_seed_assets.py
│   ├── convert_wmf_to_png.ps1
│   └── reset_m5_demo.ps1          仅重建隔离的 M5 演示数据库
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
docker compose exec backend python -m scripts.verify_m4
docker compose exec backend python -m scripts.verify_m5
docker compose exec backend python -m scripts.verify_m6
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
& "..\.conda-env\python.exe" -m scripts.verify_m3
& "..\.conda-env\python.exe" -m scripts.verify_m4
& "..\.conda-env\python.exe" -m scripts.verify_m5
& "..\.conda-env\python.exe" -m scripts.verify_m6
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
- 初始自动组卷池：45 组，其中填空 32、选择 11、判断 2；教师确认或撤回后数量会动态变化；
- 初始待入库验证：94 组；计算题与解答题保留原题和参考答案供老师核验；
- 自动组卷池覆盖新版校本审核定义的 12 个节内主属 KP、4 档难度；
- 45 组自动组卷题全部为 `machine_ready`、`source_review_status=reviewed`；
- 自动校验错误：0。

全量数据来自 `data/interim/rational_all.json`，其中 `machine_ready` 题可参加推荐组卷，`raw_answer_only` 题只进入展示层。正式知识点使用新版校本资料中的出现顺序，不再按名称排序；题库、画像、推荐和组卷统一直接使用 `KnowledgePoint.code`。

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

查询画像和推荐预览不会写入数据库。M4 的新提交、改判和撤销确认会在同一事务中复用现有重放与汇总写入服务。

## M3 组卷与发布

教师从“组卷”页面选择学生、教学范围和题量，先进行不会写库的全班预检，再原子生成草稿。草稿可换题、删题、调序或整份删除；发布时生成 `RAT-XXXX-XXXX` 实例码并冻结快照。已发布版本可以撤回并重新发布，但不能物理删除；修订版发布时自动将直接前版标记为“已被更新”。旧编号始终定位旧版，同时返回其有效、撤回或已被更新状态。

M3 只读数据验收：

```powershell
cd code\backend
$env:DATABASE_URL = 'postgresql+psycopg://postgres@localhost:55432/math_agent'
& '..\.conda-env\python.exe' -m scripts.verify_m3
```

教师可从题库页面手动录入新题，新题先进入待审核区，选择题和填空题完成结构化答案审核后可进入自动组卷池。题目与学生的“删除”采用业务归档：从当前列表、推荐和后续组卷中移除，但保留既有试卷快照、作答和审计证据。

## M4 手动批改与画像闭环

教师从“批改”页面选择当前有效的已发布学生试卷，按题号录入答案或明确标记留白。系统按发布快照执行确定性判分：

```text
choice  选项字母归一化后比较
exact   只执行题目规则明确允许的文本归一化
numeric 受限 AST + Fraction 精确计算，不使用 eval
latex_exact 相同规范 LaTeX 自动确认，其他写法进入复核
group   按 item_id 对齐小题并保留逐项结果
```

解析失败、规则不明或格式不可靠时使用 `needs_review + is_correct=null`，不会自动污染画像。教师可确认正确/错误、修正学生答案或撤销确认；题库标准答案修正并重新发布后，可显式重新评分已有提交。所有有效变化均追加完整 `AttemptRevision`，重新评分不会修改 `PaperInstanceItem` 发布快照，并在同一事务中更新 `StudentMastery`。

非选择题可在普通输入和可视化公式输入之间切换。公式区直接提供分数、平方根、n 次根、平方、幂、下标、绝对值、括号、π、正负号、乘除号、比较符号和度数符号，并可打开数字、符号、字母和希腊字母完整键盘。该键盘只解决可靠录入问题，不改变判分语义：`numeric` 仍遵循题目规则中的有理数等价开关，`latex_exact` 仍不承诺任意代数等价。

M4 只读数据验收：

```powershell
cd code\backend
$env:DATABASE_URL = 'postgresql+psycopg://postgres@localhost:55432/math_agent'
& '..\.conda-env\python.exe' -m scripts.verify_m4
```

## M5 班级学情与固定演示

前端默认进入“学情”页面。班级聚合沿用个人画像的同一证据校验与重放逻辑：`needs_review` 不进入正确率分母，未评估学生不按 0 或内部冷启动值 0.5 参与班级掌握度均值。每个知识点同时给出证据学生数、确认作答数、正确率和掌握度参考值，并可下钻到学生画像和具体 Attempt。

固定演示场景（按演示目标二选一；若需切换到另一场景，可另行执行对应命令）：

```powershell
cd code\backend
$env:DATABASE_URL = 'postgresql+psycopg://postgres@localhost:55432/math_agent'
& '..\.conda-env\python.exe' -m scripts.prepare_m5_demo --scenario m5_start
# 或：& '..\.conda-env\python.exe' -m scripts.prepare_m5_demo --scenario m5_showcase
```

`prepare_m5_demo` 是幂等构造器，但不会删除或倒带已有审计记录。需要重复恢复同一初始状态时，使用独立的 `math_agent_m5_demo` 数据库：

```powershell
cd code
.\scripts\reset_m5_demo.ps1 -Scenario m5_showcase -ConfirmResetM5Demo
```

重置脚本只允许删除精确命名的 `math_agent_m5_demo`，不会修改默认 `math_agent` 开发库，也不会关闭 AttemptRevision 的追加式保护。执行前须确保项目内 PostgreSQL 已在 55432 端口运行。

M5 只读验收：

```powershell
cd code\backend
$env:DATABASE_URL = 'postgresql+psycopg://postgres@localhost:55432/math_agent'
& '..\.conda-env\python.exe' -m scripts.verify_m5
```

生产构建已按路由懒加载，并将 ECharts 与 zrender 从页面代码中拆出。MathLive 是仅在公式录入路由加载的单模块大包，构建仍会如实报告其大于 500 kB；项目没有通过调高 Vite 告警阈值隐藏该事实。

## M6 打印、图片识别与教师复核

已发布试卷详情页提供“打印学生卷”和“上传答题区照片”。打印接口和普通 student view 都使用题目字段白名单，不返回标准答案、评分规则、解析、易错分析或教师审计字段；图片支持 JPEG、PNG、WebP，服务端会校验真实格式和像素、应用 EXIF 方向、去除元数据并重新编码。识别结果只抄读答案，提交后所有 Attempt 先进入 `needs_review`，教师对照原图确认或修正后才调用 M4 的 `grade_answer` 并重算画像。

默认配置使用 `VISION_PROVIDER=fake`，只用于离线开发和故障回退，会把每题标成“无法辨认”，不代表真实识别验收通过。接入兼容 `/chat/completions` 的视觉模型时，在本地 `.env` 配置：

```dotenv
VISION_ENABLED=true
VISION_PROVIDER=openai_compatible
VISION_MODEL=<支持图片输入的模型名>
VISION_API_BASE_URL=<供应商 API 根地址>
VISION_API_KEY=<只保存在本地环境中的密钥>
VISION_MAX_TOKENS=2048
```

推荐的本地演示模型是 `Qwen/Qwen3-VL-4B-Instruct`，使用 Instruct 而不是 Thinking 版本。通过 vLLM 在本机提供 OpenAI 兼容接口时，`VISION_API_KEY` 可以留空；只有配置了密钥时，后端才发送 `Authorization` 请求头。识别提示词会按发布快照动态传递选择题可用字母（不限于 ABCD），但不会传递标准答案。模型输出的大小写和全角字母会被归一化，超出本题选项范围、组合小题缺失或重复的结果会自动进入“存在歧义”，只能由教师修正，不能直接判错。

当前打印产物是“试卷正文 + 文末统一答题区”，不是独立答题卡；主链路要求教师手工裁剪统一答题区，再由 VLM 读取。后续 M6.1 将增加独立 A4 答题卡、稳定定位标记、不可变布局版本、逐题坐标、透视校正和自动裁剪。第一版仍只使用一个主 VLM，`PP-OCRv5` 与 `PP-FormulaNet_plus-M` 仅在真实评测证明存在特定短板后作为可选 adapter，不是同时接入的前置依赖。本阶段不引入 PaddlePaddle 重型运行时，详见项目根目录 `11_数学教学Agent_M6模型接入与答题识别优化计划.md`。

真实图片评估使用 `data/eval/m6/manifest.example.json` 作为清单模板；私人答题图片目录和结果文件已加入 `.gitignore`：

```powershell
cd code\backend
$env:DATABASE_URL = 'postgresql+psycopg://postgres@localhost:55432/math_agent'
& '..\.conda-env\python.exe' -m scripts.evaluate_m6_recognition --help
& '..\.conda-env\python.exe' -m scripts.purge_recognition_images
& '..\.conda-env\python.exe' -m scripts.verify_m6
& '..\.conda-env\python.exe' -m scripts.verify_m6 --require-image-scenario
```

`verify_m6 --require-image-scenario` 只接受由非 fake 供应商完成并提交的图片闭环。真实评估拒绝 disabled/fake 适配器和空样本；使用 `--require-thresholds` 时，还会按 manifest 中的 `acceptance_thresholds` 决定退出码。示例阈值只是演示基线，应由项目组结合真实复核负担确认。

`purge_recognition_images` 默认仅 dry-run，只有显式传入 `--confirm` 才删除超过保留期的原图；数据库中的哈希、模型版本、识别结果、Submission、Attempt 和修订审计仍会保留。

完整实现与演示交接见 `docs/13_M6_工作总结与教师演示版交接.md`。

## 下一阶段

先配置真实视觉模型并收集去身份化样本，执行准确率、漏识别率、需修改比例与教师复核耗时评估。达到项目约定阈值后，才能将 M6 标记为“教师演示版完成”；在此之前工程功能可演示，但识别质量只算待验收。
