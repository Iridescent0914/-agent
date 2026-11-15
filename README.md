# 数学教学 Agent Demo

当前已实现必做题、历史回顾、独立周总结、按九维动态素养分生成的个性化三题、提交批改、学生画像和班级学情。日常自动组卷池仅接纳选择、填空、判断题；历史画像按发布快照解释作答。系统同时具备固定答题区与 VLM 图片识别的工程闭环，真实视觉模型接入与样本质量验收暂未完成。

阶段四批量题目入库已实现 S1 暂存与 S2 识别工程链路：私有 PDF/DOCX 上传、DOCX 转 PDF、PDF 页段发送百炼 Flash、候选题校验与暂存、逐段用量及费用估算。S2 默认关闭，须输入内部操作口令并显式启动；真实质量和账单费用仍待验收。S3 已启动：题答同文件的新批次可在同一页段调用中分别提取独立题目与原文答案候选，并依据明确章节标题排除答案区重述题和参考资料条目；资料答案可保守关联为待教师确认版本。题图、分文件答案、标签建议等 S3 内容仍待开发；此入口不写入正式题库。详见[阶段四 S3 首批记录](docs/阶段四-批量题目入库/yan/S3/01_同文件一次提取与资料答案保守关联.md)及[实测修复记录](docs/阶段四-批量题目入库/yan/S3/02_题答同文件实测问题与修复.md)。

## 当前功能

- FastAPI + SQLAlchemy + PostgreSQL 后端；
- Alembic 迁移、推荐审计、发布试卷快照保护、版本生命周期、批改审计和图片识别守卫；
- 180 组完整题库（有理数139组、简单的代数式41组）；当前仓库基线为76组自动组卷题（62＋14），数据库题池随教师审核变化。有理数080保留结构化答案及最终结果判分能力，供后续视觉识别专项验证，已撤出日常自动组卷池；
- 题库种子与班级、学生、作业、作答等业务演示数据分离，默认导入不会创建旧 Demo 数据；
- 题库列表、筛选、分页、详情、已有题目编辑和业务删除 API / 页面；
- 选择题、填空题支持教师修改答案、保存审核草稿、确认入池、撤回后重新编辑和版本冲突保护；
- 填空题支持普通文本、数值和可视化公式输入；公式区内置分数、根号、幂、下标等常用按钮以及完整公式键盘，表达式以 ASCII Math 或 LaTeX 文本保存，不保存为图片；
- 学生列表、详情、历史作答、新增和业务删除 API / 页面；
- 从 0.5 开始按 `(answered_at, id)` 重放 EMA，生成 50 条学生 × 知识点画像汇总；
- 学生画像按完整章节展示知识点正确率、回顾标签和九维动态素养分，支持历史作答与班级平均；学生列表分开显示有效确认作答、待复核和历史记录数；
- 每个画像点和推荐理由可回溯到历史 Attempt、修订号与发布试卷快照；
- 教师可设置新知识 / 回顾范围、题量、题型、难度、近期去重和单知识点占比，预览规则推荐及候选缺口；
- 861 个唯一公式/图片资源转换为浏览器可显示格式（第一章 564 个，第二章 297 个，含从 Word 原生绘图恢复的036顶点数字图）；
- 第二章“简单的代数式”包含 41 组完整题库、14 组可自动判分题和 8 个知识点；
- 多学生只读预检与原子生成，每名学生拥有独立 draft PaperInstance；
- 草稿支持规则一致的备选换题、删题、连续调序和乐观锁冲突保护；
- 单份 / 批量发布、全局唯一实例码、按码精确查询和发布后复制新版本；
- 推荐参数、分项得分、证据、候选缺口与教师编辑事件均随试卷保存；
- 答案录入和修正支持选择题、精确文本、可视化公式、受限数值表达式和小题组，无法可靠判断时进入人工复核；
- 一份试卷一个 Submission、一题一个 Attempt，重复提交不重复计数，改判追加 AttemptRevision；
- 教师确认、修正答案或撤销确认后，在同一事务中按当前证据全量重算学生画像；
- 批改工作台、逐题结果、修订时间线和画像入口支持桌面与 390px 页面；
- 班级学情总览按发布快照和当前有效修订聚合，支持知识点正确率、掌握度参考均值、证据覆盖、未评估状态和学生下钻；
- 作业进度只统计每名学生的当前有效发布版本，区分未提交、待复核、已完成、草稿和历史作废版本；
- 页面级懒加载，ECharts 与 zrender 独立分包；学情页和批改筛选 URL 支持桌面与 390px 页面；
- 当前业务规则的后端自动化测试、只读一致性验收、前端 production build，以及桌面 / 390px 浏览器回归。

推荐预览仍保持只读；只有教师确认生成后才创建 Assignment 和草稿。首次录入会锁定题库中当时已发布且可机判的标准答案：录入前完成的答案修订会直接用于首次评分；只有提交之后题库再次修改，已有提交才显示“重新评分”。重新评分由教师显式触发并追加修订，始终不改写 `PaperInstanceItem` 发布快照。视觉模型置信度仅用于展示，所有图片作答初始均为 `needs_review`，教师确认前不会更新画像。

## 目录

```text
code/
├── backend/                       FastAPI、模型、迁移、种子、验收和测试
├── frontend/                      React + TypeScript + Ant Design
├── data/
│   ├── seed/                      正式题库与当前演示场景配置
│   ├── assets/rational/           第1章有理数原始资源
│   ├── assets/expr/               第2章简单的代数式原始资源
│   ├── web/rational/              第1章 Web 资源与 manifest
│   └── web/expr/                  第2章 Web 资源与 manifest
├── scripts/
│   ├── build_rational_m0.py
│   ├── validate_m0.py
│   ├── convert_seed_assets.py
│   └── convert_wmf_to_png.ps1
├── docker-compose.yml
└── .env.example
```

## 运行方式与快速开始

项目统一使用 Docker Compose 运行，后端测试、迁移、种子导入和验收均以容器环境为准，避免维护第二套 Python 和 PostgreSQL 环境。前端 Playwright 浏览器回归是唯一例外：它在 Windows 主机上使用 Node.js 和 Microsoft Edge 执行，但仍连接 Docker 中的后端和数据库。

### Docker Compose

以下 Docker 命令均从项目根目录 `D:\desktop\math-agent\code` 执行。

#### 首次运行

只在第一次创建容器、镜像和数据库卷时执行：

```powershell
cd "D:\desktop\math-agent\code"
Copy-Item .env.example .env
docker compose up -d --build
docker compose exec backend python -m scripts.seed_database --chapter all
docker compose ps
```

`seed_database` 只初始化知识点和题库，不创建班级、学生、作业、试卷、作答或画像。后端容器每次启动都会自动执行 `alembic upgrade head`。业务演示数据通过当前版本的独立脚本创建；没有学生时不需要执行 `rebuild_mastery --all`。

#### 日常启动

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

#### 代码或依赖发生变化时

当前源码通过目录挂载进入容器，普通 Python / TypeScript 源码修改会由后端 reload 和 Vite 自动加载，通常不需要重新构建镜像。

获取新代码后应按实际改动类型执行，不需要每次重新构建镜像或安装 npm 包。**每一行命令执行完成后再执行下一行，不要粘连成一行。**

##### 仅修改普通源码

仅修改普通 `.py`、`.ts`、`.tsx` 或 CSS 源码时，如果容器已经运行，不需要执行更新命令，后端 reload 和 Vite 会自动加载；容器未运行时只需：

```powershell
cd "D:\desktop\math-agent\code"
docker compose up -d
```

##### 前端修改后页面没有变化

Windows 目录挂载到 Linux Docker 容器时，Vite 偶尔可能没有收到文件变更事件。此时容器内源码虽然已经更新，但 Vite 仍在返回旧的转换模块。先重启前端开发服务，再在浏览器中按 `Ctrl+F5` 强制刷新：

```powershell
cd "D:\desktop\math-agent\code"
docker compose restart frontend
```

该命令只重启前端容器并重建 Vite 内存模块图，不会清空数据库、重新安装依赖或重新构建镜像。

如果重启后页面仍未更新，强制重新创建前端容器：

```powershell
cd "D:\desktop\math-agent\code"
docker compose up -d --force-recreate frontend
```

重新创建前端容器同样不会删除 PostgreSQL 数据卷。不要为解决前端缓存问题执行 `docker compose down -v`。

##### 后端修改后接口没有变化

后端通常会由 Uvicorn 自动 reload；如果接口仍是旧行为，重启后端服务：

```powershell
cd "D:\desktop\math-agent\code"
docker compose restart backend
```

##### 修改迁移或种子数据

包含 Alembic 迁移、知识点或题库种子数据变化时执行：

```powershell
cd "D:\desktop\math-agent\code"
docker compose up -d
docker compose exec backend python -m alembic upgrade head
docker compose exec backend python -m scripts.seed_database --chapter all
docker compose ps
docker compose exec backend python -m alembic current
docker compose exec backend python -m scripts.verify_expr
```

`alembic upgrade head` 和默认的 `seed_database` 均可重复执行，并且不会创建或覆盖班级、学生和作答数据。已有学生且确实需要重建画像时，再单独执行 `python -m scripts.rebuild_mastery --all`。后端容器从停止状态启动时本身也会执行迁移；显式运行迁移可以覆盖“容器原本已经运行，拉取代码后没有重新触发启动命令”的情况。

##### 修改后端依赖

只有修改了 `backend/pyproject.toml`、后端 Dockerfile 或 Python 依赖时，才需要重新构建后端：

```powershell
cd "D:\desktop\math-agent\code"
docker compose build backend
docker compose up -d backend
```

##### 修改前端依赖

只有修改了 `frontend/package.json`、`frontend/package-lock.json`、前端 Dockerfile 或 npm 依赖时，才需要安装前端依赖：

```powershell
cd "D:\desktop\math-agent\code"
docker compose build frontend
docker compose run --rm frontend npm ci
docker compose up -d frontend
```

##### 修改 `.env` 或 Compose 配置

修改 `.env`、端口、跨域来源或 `docker-compose.yml` 后，重新创建受影响的服务。无法确定影响范围时可以重新创建全部服务；该操作不会删除 PostgreSQL 数据卷：

```powershell
cd "D:\desktop\math-agent\code"
docker compose up -d --force-recreate
docker compose ps
```

#### 状态确认、访问与停止

可以通过以下命令确认数据库迁移和主要工作流状态：

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
PostgreSQL（宿主机）：localhost:55433
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

#### 镜像构建网络故障

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

## 资源转换

已生成的 Web 资源按章节分别位于 `data/web/rational/` 和 `data/web/expr/`，每个目录各自维护 `asset_manifest.json`。重新构建任一章节数据后执行：

```powershell
cd "D:\desktop\math-agent\code"
docker compose exec -w /app backend python scripts/convert_seed_assets.py
```

脚本只处理正式种子题实际引用的资源，并生成 `asset_manifest.json`。Windows 优先使用 ImageMagick，未安装时使用项目内 PowerShell + GDI+ 转换器。转换失败会返回非零状态，不会覆盖原始 WMF。

## 测试与验收

### Docker 后端测试与前端构建

```powershell
cd "D:\desktop\math-agent\code"
docker compose exec backend pytest -q
docker compose exec backend python -m scripts.verify_expr
docker compose exec backend python -m scripts.verify_m3
docker compose exec backend python -m scripts.verify_m4
docker compose exec backend python -m scripts.verify_m5
docker compose exec backend python -m scripts.verify_m6
docker compose exec frontend npm run build
```

### 前端浏览器回归（Windows 主机）

Playwright 会自行启动 Vite 测试服务，因此运行前应先停止占用 `5173` 端口的 Docker 前端；后端和数据库保持运行。首次执行或 `package-lock.json` 变化后先运行 `npm ci`：

```powershell
cd "D:\desktop\math-agent\code"
docker compose stop frontend
cd ".\frontend"
npm ci
npm run test:e2e
cd ".."
docker compose start frontend
```

如果测试中断，仍可单独执行 `docker compose start frontend` 恢复日常前端。

### 题库数据回归

```powershell
cd "D:\desktop\math-agent\code"
docker compose exec -w /app backend python scripts/validate_m0.py
```

## 种子导入规则

默认命令只导入知识点与题库：

```powershell
docker compose exec backend python -m scripts.seed_database --chapter all
```

阶段一旧版 S001～S005、未设置年级/学年的 Demo 班和三次旧历史作答已停止支持。种子导入器不再提供旧演示兼容开关；演示数据统一由符合当前筛选、组卷和画像规则的独立脚本创建。

### 创建当前版本演示数据

先完成迁移和题库导入，再显式创建当前演示场景：

```powershell
docker compose exec backend python -m scripts.prepare_current_demo
```

该命令读取 `data/seed/current_demo_scenario.json`，幂等创建：

- 六年级 Demo 班，年级为“六年级”，学年为 `2026-2027`；
- 5 名匿名演示学生；
- 2 份全班统一必做题且已全部批改的来源作业；
- 1 份按当前规则生成的必做题 + 逐学生历史回顾作业；
- 1 份独立周总结；
- 可由真实 Attempt 和修订记录回放的“是 / 否 / 弱”状态及学生画像。

演示构造器调用当前组卷、发布、提交、确定性判分、动态素养评分、知识点状态和周总结服务，不生成尚未实现的个性化题。重复执行不会重复创建班级、学生、作业或作答，并会幂等同步每名学生的 9 条动态素养分。它不会随 `docker compose up` 自动运行；其他协作者拉取代码后需要在自己的数据库显式执行一次。

### 清理旧业务与演示数据

清理脚本默认只预览当前数据库、保留表、待清理表和各表数量，不写数据库：

```powershell
docker compose exec backend python -m scripts.cleanup_business_data
```

确认预览中的数据库名和数量后，必须同时传入 `--confirm` 和完全一致的数据库名：

```powershell
docker compose exec backend python -m scripts.cleanup_business_data --confirm --expected-database math_agent
```

脚本只清空班级、学生、作业、试卷、提交、作答、修订、画像、知识点状态和识别业务表，保留 `questions`、`knowledge_points` 与 `alembic_version`。它不使用 `CASCADE`，在同一事务内执行并复核保留表数量；如果数据库名不一致、必需表缺失或出现尚未分类的新表，会拒绝执行并回滚。该命令只影响当前连接的数据库，不会通过 Git 清理其他协作者的数据库。

稳定身份键：

- `KnowledgePoint.code`；
- `Question.original_qid`；
- `Student.student_code`；
- `Assignment.seed_key`；
- `PaperInstance.instance_code`。

第二次执行默认 `seed_database` 不会生成重复知识点或题目，也不会创建学生、作业、试卷或作答。题目的 `correct_answer`、多空 `items`、归一化规则和资源列表均按 JSONB 原样保存。

### 题目答案的小题—空统一格式

选择、填空、判断、计算和简答题的题库答案统一使用 `answer-units-v1`。每个可独立作答的位置都是一个叶子项，稳定编号为 `sub_{小题号}.blank_{空号}`；同一空的同义答案放在 `accepted_answers`，不能再用数组表示多个空。计算题和简答题使用 `manual`，保存分小题的人工评分参考答案，不进入自动判分池。

```json
{
  "kind": "group",
  "schema_version": "answer-units-v1",
  "items": [
    {
      "item_id": "sub_2.blank_1",
      "subquestion_no": 2,
      "blank_no": 1,
      "order_no": 1,
      "answer": "这个数",
      "accepted_answers": ["这个数", "原数"],
      "answer_match_type": "exact"
    }
  ]
}
```

迁移脚本默认只预演；确认后显式提交数据库。它会同步整理仓库 JSON，缺失或无法可靠对应的答案保留为空、标记 `answer_units_need_review` 并退出自动判分池，不猜测答案。已发布试卷继续使用生成时的答案快照，不随题库迁移变化。

```powershell
python backend/scripts/normalize_answer_units.py --skip-database
docker compose exec backend python -m scripts.normalize_answer_units --skip-files
docker compose exec backend python -m scripts.normalize_answer_units --skip-files --apply-database
```

完整的待人工核对清单见 `data/audit/answer_unit_normalization_report.json`。

阶段五对选择、填空、判断题的逐题原因、修复依据和剩余4组阻塞项，见[首轮入池修复记录](docs/阶段五-自动组卷池与题目答案治理/lin/02_选择填空判断题逐题排查与首轮入池修复记录.md)。修复使用 `data/config/question_pool_corrections.json`，仓库与数据库分别通过 `scripts/repair_question_pool.py` 显式应用；既有试卷快照和历史批改不随题库修复自动改写。

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

## 第一章“有理数”题库分层结果

- 完整题库：139 组，全部可在题库页面查看；
- 初始自动组卷池：45 组，其中填空 32、选择 11、判断 2；教师确认或撤回后数量会动态变化；
- 初始待入库验证：94 组；计算题与解答题保留原题和参考答案供老师核验；
- 自动组卷池覆盖新版校本审核定义的 12 个节内主属 KP、4 档难度；
- 45 组自动组卷题全部为 `machine_ready`、`source_review_status=reviewed`；
- 自动校验错误：0。

全量数据来自 `data/interim/rational_all.json`，其中 `machine_ready` 题可参加推荐组卷，`raw_answer_only` 题只进入展示层。正式知识点使用新版校本资料中的出现顺序，不再按名称排序；题库、画像、推荐和组卷统一直接使用 `KnowledgePoint.code`。

待入库的选择题和填空题可从题目详情页点击“审核答案并进入组卷池”。老师可先保存审核草稿，再执行“校验并确认入池”。已入池题目可执行“撤回并重新编辑”，撤回后不再参与新组卷，修改并重新校验后可再次入池。每次保存、入池和撤回都会记录操作人、版本和前后答案。数值只接收整数或小数，分数、根式等请选择公式输入。当前公式采用 LaTeX 精确匹配，暂不承诺不同代数写法的等价判定。

已经保存过教师审核记录的答案属于业务数据；以后再次执行 `seed_database` 时会保留其答案、判分规则、审核版本和入池状态。

### 第二章“简单的代数式”

第二章使用新版 `六年级_数学_校本审核/新/expr_简单的代数式.docx`，构建命令为：

```powershell
cd "D:\desktop\math-agent\code"
docker compose exec -w /app backend python scripts/build_expr_m0.py
docker compose exec -w /app backend python scripts/validate_m0.py --prefix expr --source-root . --chapter-code math.g6.expr --min-seed 1 --max-seed 50 --expected-sections 4 --expected-knowledge-points 8
docker compose exec -w /app backend python scripts/convert_seed_assets.py
```

产物为：

- `data/interim/expr_all.json`：41 组完整题目；
- `data/seed/expr_questions.json`：14 组 `machine_ready` 题目；第28题已完成逐空核对，第36题已恢复原图、统一四小题编号并入池；仓库基线仍待验证的是第13、32组，数据库保留教师审核结果；
- `data/seed/expr_knowledge_tree.json`：第 2 章 4 个节、8 个正式知识点；
- `data/audit/expr_import_audit.csv` 和 `data/audit/expr_m0_report.json`：审计结果。

题库导入支持按章节执行。各章节默认都只导入知识点与题目，不创建学生或历史演示作业：

```powershell
cd "D:\desktop\math-agent\code"
docker compose exec backend python -m scripts.seed_database --chapter expr
docker compose exec backend python -m scripts.verify_expr
```

也可以在空库或需要同时重导两个章节时执行：

```powershell
cd "D:\desktop\math-agent\code"
docker compose exec backend python -m scripts.seed_database --chapter all
```

第二章完整题库中 25 组解答题和部分复杂填空题保留在题库展示层，当前不进入自动组卷池；这与第一章的“完整题库 / 自动组卷池”分层规则一致。

## 学生画像重算

掌握度、素养分和知识点状态均是可重建结果，证据来自 Attempt、当前 AttemptRevision 和发布快照。统一入口按学生、班级或全部在读学生处理；默认排除归档学生，每名学生独立提交，单学生三类画像一起成功或回滚。失败报告包含学生、异常作答ID和原因，部分失败时返回非零退出码。

```powershell
cd "D:\desktop\math-agent\code"
docker compose exec backend python -m scripts.rebuild_profiles --student-code DEMO-S001 --dry-run
docker compose exec backend python -m scripts.rebuild_profiles --class-id <班级UUID> --dry-run
docker compose exec backend python -m scripts.rebuild_profiles --all --dry-run
# 检查前后差异后，需要写回时执行：
docker compose exec backend python -m scripts.rebuild_profiles --all
```

`rebuild_mastery` 保留为上述统一命令的兼容入口。`--as-of` 表示使用当前最终修订并限制作答时间的计算截止时点，不表示恢复过去时刻的修订状态。预演输出三类画像的前后结果并回滚写入。

历史知识点归属使用发布快照，题库归档或当前主知识点调整不迁移过去的作答。未知快照编码显式报告异常，不按当前题目猜补；确需修正历史分类时，应另行审核明确迁移范围。历史计算题、解答题的判分结果保留，以 `unsupported_profile_question_type` 标记不参与当前画像；归属损坏、修订缺失等异常继续阻止重建。

查询画像和推荐预览不会写入数据库。新提交、图片确认、改判、撤销确认和重新判分会在同一事务中刷新知识点掌握度、动态素养分和知识点状态。尚未形成确认批改结果的试卷可以撤回，撤回后已有未确认作答保留用于历史查看和审计，但不参与当前画像；重新发布后会重新纳入。试卷一旦存在自动确认或教师确认的批改结果（包括修订历史中的确认结果），就不允许撤回，接口返回冲突且画像保持不变。被新版替代的旧版本仍退出当前画像，并在发布修订版的同一事务中完成画像回放。

本轮维护与验证见[画像证据、统计与重建维护记录](docs/阶段三-画像与个性化题目/lin/21_画像证据统计与重建维护记录.md)及[080撤池与专项验证保留记录](docs/阶段五-自动组卷池与题目答案治理/lin/07_080撤池与视觉识别验证能力保留记录.md)。

知识点回顾状态会核验推荐审计中的来源作答：只有来源日常作业当前仍为错、知识点一致、来源试卷仍有效且两次错误之间没有已确认正确作答时，历史回顾再次答错才记为“否”；周总结的“弱”继续建立在这条有效连续错误证据链上。来源作答被改判、撤回或被中间正确作答打断后，重放会自动撤销相应标签。

个性化题换题会保留不可变的初始生成依据，并以追加记录保存每次换题的前后题目、候选题理由、操作时间和换题当时的班级复用人数。试卷页面中的当前推荐理由始终属于当前题目；“初始分配复用”和“换题时复用”是两个独立快照，不表示实时人数。

班级学情通过批量上下文加载器读取全班画像。知识点目录、作答、知识点状态和素养分均按学生集合查询；素养物化记录缺失时也会批量读取证据后在内存中分别回放，避免班级人数增加时产生逐生查询。

## 动态素养分重建

`StudentLiteracyScore` 是由当前有效 Attempt、AttemptRevision 和已发布题目快照确定性回放得到的物化结果。计算使用发布时保存的题目难度、题型和核心素养，按 `Asia/Shanghai` 自然日执行单日限幅与未练回归。数据库内部保留 4 位小数；学生画像和班级学情接口使用 `ROUND_HALF_UP` 返回整数，个性化基础星级也按同一个显示整数分档。

后端维护任务会在服务启动时立即补算，并在上海时区跨日后刷新仍停留在旧日期的 `student_literacy_scores`。正在提交或改判、已持有学生行锁的记录会被本轮跳过，由业务事务自身或下一次维护刷新，维护任务不会阻塞作答业务。

先预演一个学生或全部学生：

```powershell
docker compose exec backend python -m scripts.rebuild_literacy_scores --student-code DEMO-S001 --dry-run
docker compose exec backend python -m scripts.rebuild_literacy_scores --all --dry-run
```

确认输出后写入当前数据库：

```powershell
docker compose exec backend python -m scripts.rebuild_literacy_scores --all
```

需要固定验收时点时，可以提供带时区的 ISO 时间：

```powershell
docker compose exec backend python -m scripts.rebuild_literacy_scores --all `
  --as-of 2026-10-31T12:00:00+08:00 --dry-run
```

命令会为每名学生同步 9 条素养分记录；重复使用相同证据和相同日期执行时不会重复创建或修改数据。学生画像和班级学情接口读取该物化结果并返回 `score_source=computed`、`rule_version=literacy-dynamic-v2`；若服务器刚迁移尚未重建，接口会只读回放当前有效证据作为兜底，不会在 GET 请求中写数据库。

题目核心素养只允许 HTML 图谱的九个正式维度：抽象能力、运算能力、几何直观、空间观念、推理能力、数据观念、模型观念、应用意识、创新意识。多个维度使用 `、` 分隔，由主属 KP 的完整集合派生。“数据意识”是小学对应项，不作为“数据观念”的别名；含该标签的旧有效作答或有实际学习记录的旧分数需要人工核对，系统不会自动改名。题目草稿可以暂存为“待标注”，但进入可机判题池和发布试卷前必须完成正式标注。

从旧八维评分环境切换前，先备份数据库并预演。默认命令会回滚评分写入；`--apply` 才提交，任何学生有未解决的有效证据或旧维度学习记录时整批停止。报告保存每名学生的前后分数，并核对8张业务保护表的数量与内容哈希；不修改试卷快照、作答和历史选题依据。

```powershell
docker compose exec -T backend python -m scripts.transition_literacy_dimensions --output /app/data/uploads/literacy_transition_preview.json
docker compose exec -T backend python -m scripts.transition_literacy_dimensions --apply --output /app/data/uploads/literacy_transition_applied.json
```

九维目录、评分、组卷和页面的完整调整记录见[阶段三记录23](docs/阶段三-画像与个性化题目/lin/23_九维素养评分与组卷画像统一记录.md)。

## 组卷与发布

教师从“组卷”页面选择学生、教学范围和题量，先进行不会写库的全班预检，再原子生成草稿。草稿可换题、删题、调序或整份删除；发布时生成 `RAT-XXXX-XXXX` 实例码并冻结快照。尚未形成确认批改结果的已发布版本可以撤回并重新发布；一旦出现自动确认或教师确认的批改结果，页面会禁用撤回，后端也会拒绝该操作。已发布版本不能物理删除；修订版发布时自动将直接前版标记为“已被更新”。旧编号始终定位旧版，同时返回其有效、撤回或已被更新状态。

组卷与发布只读数据验收：

```powershell
cd "D:\desktop\math-agent\code"
docker compose exec backend python -m scripts.verify_m3
```

题库由源文件和导入脚本维护，单题上传入口及新增题目接口已移除。教师可编辑已有题目内容、补充图片并审核结构化答案；内容修改后需重新审核才能进入自动组卷池。题目与学生的“删除”采用业务归档：从当前列表、推荐和后续组卷中移除，但保留既有试卷快照、作答和审计证据。

## 手动批改与画像闭环

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

批改与画像只读数据验收：

```powershell
cd "D:\desktop\math-agent\code"
docker compose exec backend python -m scripts.verify_m4
```

## 班级学情

前端默认进入“学情”页面。班级聚合沿用个人画像的同一证据校验与重放逻辑：`needs_review` 不进入正确率分母，未评估学生不按 0 或内部冷启动值 0.5 参与班级掌握度均值。每个知识点同时给出证据学生数、确认作答数、正确率和掌握度参考值，并可下钻到学生画像和具体 Attempt。

班级学情只读验收：

```powershell
cd "D:\desktop\math-agent\code"
docker compose exec backend python -m scripts.verify_m5
```

生产构建已按路由懒加载，并将 ECharts 与 zrender 从页面代码中拆出。MathLive 是仅在公式录入路由加载的单模块大包，构建仍会如实报告其大于 500 kB；项目没有通过调高 Vite 告警阈值隐藏该事实。

## 打印、图片识别与教师复核

已发布试卷详情页提供“打印学生卷”和“上传答题区照片”。打印接口和普通 student view 都使用题目字段白名单，不返回标准答案、评分规则、解析、易错分析或教师审计字段；图片支持 JPEG、PNG、WebP，服务端会校验真实格式和像素、应用 EXIF 方向、去除元数据并重新编码。识别结果只抄读答案，提交后所有 Attempt 先进入 `needs_review`，教师对照原图确认或修正后才调用确定性判分服务并重算画像。

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

当前打印产物是“试卷正文 + 文末统一答题区”，不是独立答题卡；主链路要求教师手工裁剪统一答题区，再由 VLM 读取。后续识别优化将增加独立 A4 答题卡、稳定定位标记、不可变布局版本、逐题坐标、透视校正和自动裁剪。第一版仍只使用一个主 VLM，`PP-OCRv5` 与 `PP-FormulaNet_plus-M` 仅在真实评测证明存在特定短板后作为可选 adapter，不是同时接入的前置依赖。本阶段不引入 PaddlePaddle 重型运行时，详见项目根目录 `11_数学教学Agent_M6模型接入与答题识别优化计划.md`。

真实图片评估使用 `data/eval/m6/manifest.example.json` 作为清单模板；私人答题图片目录和结果文件已加入 `.gitignore`：

```powershell
cd "D:\desktop\math-agent\code"
docker compose exec backend python -m scripts.evaluate_m6_recognition --help
docker compose exec backend python -m scripts.purge_recognition_images
docker compose exec backend python -m scripts.verify_m6
docker compose exec backend python -m scripts.verify_m6 --require-image-scenario
```

`verify_m6 --require-image-scenario` 只接受由非 fake 供应商完成并提交的图片闭环。真实评估拒绝 disabled/fake 适配器和空样本；使用 `--require-thresholds` 时，还会按 manifest 中的 `acceptance_thresholds` 决定退出码。示例阈值只是演示基线，应由项目组结合真实复核负担确认。

`purge_recognition_images` 默认仅 dry-run，只有显式传入 `--confirm` 才删除超过保留期的原图；数据库中的哈希、模型版本、识别结果、Submission、Attempt 和修订审计仍会保留。

## 下一阶段

先配置真实视觉模型并收集去身份化样本，执行准确率、漏识别率、需修改比例与教师复核耗时评估。达到项目约定阈值后，才能将视觉识别标记为“教师演示版完成”；在此之前工程功能可演示，但识别质量只算待验收。
