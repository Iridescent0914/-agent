# 数学教学 Agent Demo

当前已实现必做题、历史回顾、独立周总结、按九维动态素养分生成的个性化三题、提交批改、学生画像和班级学情。日常自动组卷池仅接纳选择、填空、判断题；历史画像按发布快照解释作答。系统已接入百炼视觉模型，具备多页提取、整卷核对录入及等价建议教师采用的工程链路；真实样本质量验收尚未完成。

阶段四批量题目入库已实现 S1 暂存、S2 PDF/DOCX 识别工程链路，以及 S3 的整批去重、答案提取与配对、题图关联、标签建议和仅题目批次的答案生成。S4 已实现正式题库精确查重、有索引的相近题召回，以及查重证据和教师决定的保存、失效提示。模型效果与费用、相近题召回质量仍待真实样本验收；正式题图资产、完整审核和事务性入库尚未完成，此入口不会写入正式题库。详见[阶段四开发计划](docs/阶段四-批量题目入库/yan/阶段开发计划.md)。

## 当前功能

阶段六已实现S1内部基础：持久化等价建议任务、逐次调用审计、租约恢复和逐空质量反馈，默认关闭，仅供开发环境使用。建议和反馈不自动改变判分或画像。详见[S1开发记录](docs/阶段六-答题卡与识别批改/lin/S1/01_S1调用审计与质量反馈基础记录.md)。

S2已增加逐空规则候选、只读预检、批改页建议和质量核对侧栏，以及百炼文本模型适配与独立费用检查。仓库默认使用fake；真实模式需本地填写API Key，默认模型为`qwen3.7-flash`，本库累计限20次、估算1元，超时等用量未知时暂停。未进行真实模型调用和质量验收，见[S2离线记录](docs/阶段六-答题卡与识别批改/lin/S2/01_S2逐空候选与建议复核离线实现记录.md)及[百炼接入与配置指引](docs/阶段六-答题卡与识别批改/lin/S2/02_S2百炼文本模型接入与配置记录.md)。

S3-A已完成视觉接入前的离线准备：已发布试卷逐空清单、人工指定页面清单、逐页响应的缺页/重复页/错码检查和结果合并。只读接口仅供阶段六内部开发，未接新多页上传或真实视觉调用，不生成正式成绩。后续主流程为整份答题卡提取后统一进行规则与必要的等价判断，不新增独立答案对试验页面；详见[S3准备记录](docs/阶段六-答题卡与识别批改/lin/S3/01_S3逐空清单与多页合并离线准备记录.md)。

S3-B新增固定分页的`answer-card-v3`答题区模板及不可覆盖的逐页产物清单。应用0034迁移且阶段六内部功能启用后，原“打印 / 保存 PDF”会在后台准备答题区，统一输出“前面题目、后面另页答题区”的一份学生版试卷；不新增独立答题卡打印入口。答题页带醒目标题、四角标记、产物编号及页码，相同内容复用模板。迁移、使用与验证见[S3-B记录](docs/阶段六-答题卡与识别批改/lin/S3/02_S3固定分页PDF与冻结产物记录.md)。

S3-C已增加多页作答照片管理。应用0035迁移并启用阶段六内部功能后，从试卷详情“上传答题照片”或批改页“管理答题页照片”进入：按纸面产物编号选择记录，逐张核对答题页码，可批量上传、补拍、查看缺页和切换历史照片。收齐仅表示照片齐全，尚未调用多页视觉模型，也不生成成绩。详见[S3-C记录](docs/阶段六-答题卡与识别批改/lin/S3/03_S3作答照片采集与有效页管理记录.md)。

S3-D已完成新版逐页协议、模拟任务及百炼视觉HTTP/费用控制，S3-E已补齐整卷核对后录入现有批改和画像的链路。应用0037迁移后，同一照片页面支持逐页状态、重试、取消、显式重跑、调用记录及整卷修订；补拍使旧核对清单失效，未变化页面提取结果可复用。默认`STAGE6_VISION_PROVIDER=fake`；真实模式使用独立`STAGE6_VISION_*`配置，默认`qwen3.8-flash`，数据库累计30次、预估2元上限，用量不明时暂停，不自动降级。用户已反馈小样本提取成功，真实质量尚未验收。见[模拟记录](docs/阶段六-答题卡与识别批改/lin/S3/04_S3逐页视觉协议与模拟任务记录.md)、[百炼视觉接入与配置](docs/阶段六-答题卡与识别批改/lin/S3/05_S3百炼视觉HTTP与费用控制接入记录.md)和[整卷核对与录入](docs/阶段六-答题卡与识别批改/lin/S3/06_S3整卷抄录核对与批改录入记录.md)。

S5-A已在原批改页增加“整卷等价判断”：先检查已提交答案，统一发起必要的逐空任务，复用已有结果；新增受限短中文术语候选，`+30/30`等明确数值结果继续走规则。有效、确定的真实建议经教师按题采用后保留原答、追加评分修订并刷新画像；模拟、无法确定和过期结果不能采用。无需重新提取现有答题照片，沿用文本模型配置，无新增迁移。详见[整卷判断与教师采用记录](docs/阶段六-答题卡与识别批改/lin/S5/01_整卷等价判断调度与教师采用记录.md)。按卷识别错误记录、错误率阈值和低正确率异常提示继续暂缓。

S5-B已将新文本任务升级为“结合题干判断本空是否可接受”：后端读取试卷发布快照，保留公共条件并标记`【本空】`，页面可展开核对实际使用的题干。缺图、空位错配或题干不完整时提示人工复核；模型不接收解析、其他空答案、身份或画像。新版V2结果显示“本空可接受/本空不可接受/无法确定”；旧的无上下文结果保留历史，不作为新版结果复用。刷新已录入试卷的批改页后先检查整卷，再明确发起新版任务即可，无需重新视觉提取。新任务正常计入原有调用/费用上限，本轮开发未调用真实模型；见[S5-B上下文协议及操作记录](docs/阶段六-答题卡与识别批改/lin/S5/02_结合题干的答案可接受性判断记录.md)。

- FastAPI + SQLAlchemy + PostgreSQL 后端；
- Alembic 迁移、推荐审计、发布试卷快照保护、版本生命周期、批改审计和图片识别守卫；
- 180 组完整题库（有理数139组、简单的代数式41组）；来源文件基线为76组自动组卷题，执行共享审核同步后为77组（62＋15）。各机器的数据库不会通过Git自动同步，命令见下文。有理数080保留结构化答案及最终结果判分能力，供后续视觉识别专项验证，已撤出日常自动组卷池；
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
docker compose exec backend python -m scripts.sync_question_catalog --apply
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
docker compose exec backend python -m scripts.sync_question_catalog
docker compose exec backend python -m scripts.sync_question_catalog --apply
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

已经保存过教师审核记录的答案属于业务数据；以后再次执行 `seed_database` 时会保留其答案、判分规则、审核版本和入池状态。对已编辑内容或经过共享同步的记录，也保留题型和内容，避免把解答题重新导回填空题。

### 协作者之间同步题库审核结果

Git同步代码和题库文件，各自PostgreSQL数据卷独立。网页中保存的入池、撤回和题型修改不会随`git pull`自动传播。2026-11-20已将本地遗漏的E013入池、R002答案审核、R070与E032解答题归类整理为`data/config/question_catalog_reviews.json`；原始目录与历史修复依据保留。共享目标是180组中的77组自动组卷题；两道解答题仍在题库中，退出选择/填空候选。

已经有题库的协作者拉取代码后，在项目根目录执行：

```powershell
git pull --ff-only
# 先预览题号、字段变化和 conflicts
docker compose exec backend python -m scripts.sync_question_catalog
# 无冲突时备份当前待修改题目，再写库
docker compose exec backend python -m scripts.sync_question_catalog --apply
# 再次检查应为 changes: []
docker compose exec backend python -m scripts.sync_question_catalog
```

脚本只同步79组来源选择/填空/判断题及其中的明确归类变更，不复制学生、试卷、作答或画像，不删除协作者新增的题目。`target_pool_count`为该范围的共享目标77，`database_pool_count`为当前数据库总入池量；自有新增题目可能使总数不同，`outside_scope_ready_ids`会列出范围外已入池题号。

默认只预览；出现`conflicts`时退出码为2，整批不写入，不提供强制覆盖选项。`divergent_teacher_review`表示该机器存在不同的人工审核，需要逐题核对；`archived_question`保留归档决定；`missing_question_run_seed_first`则先执行`seed_database --chapter all`再预览同步。同步前的题目及完整审核历史备份在`data/uploads/question_catalog_sync/`，已被Git忽略。重复执行不会增加修订，不自动重评既有学生成绩，详见[共享题库审核同步记录](docs/阶段五-自动组卷池与题目答案治理/lin/08_共享题库审核结果同步与数量对齐记录.md)。

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

历史知识点归属使用发布快照，题库归档或当前主知识点调整不迁移过去的作答。未知快照编码显式报告异常，不按当前题目猜补；确需修正历史分类时，应另行审核明确迁移范围。历史计算题、解答题的判分结果保留，以 `unsupported_profile_question_type` 标记不参与知识点掌握度和素养画像；归属损坏、修订缺失等异常继续阻止重建。

学生详情的画像顶部新增“历史累计正确率”：答对题组数 / 已确认题组数，覆盖全部年级、全部章节和作答时间，不随页面章节筛选改变。同一题重复练习分别计入，每次作答只取当前有效判定，多空按整题组；包括有有效确认结果的历史计算/解答题。待复核、失效试卷和无效证据不计入分母，无确认记录时显示“—”。接口 `GET /api/v1/students/{id}/profile` 返回 `historical_accuracy`，查询实时只读计算，无需新增迁移或手动重建。按卷记录视觉提取错误、质量接受和低正确率异常均暂不实现，0.6仅为后续候选；见[阶段六记录02](docs/阶段六-答题卡与识别批改/lin/02_历史累计正确率与视觉识别后续口径记录.md)。

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

学生答题卡现按发布时参考答案的“小题—空”结构生成，并分为必做题、个性化、历史回顾三块；保留原试卷题号，多空自动换行。A4打印时答题卡独立起页，续页重复显示学生编号、试卷短码和版本。周总结单独分区，缺失模块记录的旧题保留在“未分类题目”。布局版本为 `answer-card-v2`；实现及验收见[阶段六排版记录](docs/阶段六-答题卡与识别批改/lin/01_参考答案驱动的分区答题卡排版记录.md)。后续答题卡识别和答案比对也归入[阶段六](docs/阶段六-答题卡与识别批改/00_阶段说明.md)记录。

已发布试卷详情页提供“打印学生卷”和“上传答题照片”。打印接口和普通 student view 都使用题目字段白名单，不返回标准答案、评分规则、解析、易错分析或教师审计字段；图片支持 JPEG、PNG、WebP，服务端会校验真实格式和像素、应用 EXIF 方向、去除元数据并重新编码。新版识别仅抄读答案，先在整卷清单中对照原图核对或修正；明确确认录入后才创建正式作答、调用确定性判分并重算画像。不确定的评分仍进入待复核，不能补成错误。

新版多页答题页提取使用以下本地配置；Key、Base URL和模型必须属于匹配的北京地域/业务空间。Base URL填到`/compatible-mode/v1`，不要追加`/chat/completions`。填写后执行`docker compose up -d --force-recreate backend`，仅restart不会重新读取Compose环境变量。详细限制及费用含义见[接入记录](docs/阶段六-答题卡与识别批改/lin/S3/05_S3百炼视觉HTTP与费用控制接入记录.md)。

```dotenv
STAGE6_ENABLED=true
STAGE6_VISION_PROVIDER=bailian
STAGE6_VISION_API_KEY=<本地填写Key>
STAGE6_VISION_BASE_URL=https://<真实WorkspaceId>.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
STAGE6_VISION_MODEL=qwen3.8-flash
STAGE6_VISION_MAX_CALLS=30
STAGE6_VISION_MAX_BUDGET_CNY=2
```

从现有试卷详情进入“上传答题照片”，核对产物并收齐答题页，确认照片中的姓名、学号等个人信息已遮挡后点击“运行真实提取”。服务只接收本轮人工核对的有效照片；不会自动遮挡照片中的个人信息。处理后刷新任务、查看本页抄录及预估费用。在第5块点击“合并最新整卷提取结果”，逐空修正并逐页核对、保存清单，再明确“确认录入并判分”，最后进入批改页查看成绩与画像，并可使用“整卷等价判断”统一发起必要的文本建议、按题确认采用。已有有效提取结果无需重跑；保存清单不会判分。补拍、重跑或标答变化使旧清单过期，不能覆盖已有提交。详见[S3-E开发及操作记录](docs/阶段六-答题卡与识别批改/lin/S3/06_S3整卷抄录核对与批改录入记录.md)及[S5-A整卷判断记录](docs/阶段六-答题卡与识别批改/lin/S5/01_整卷等价判断调度与教师采用记录.md)。配置无效不退回模拟；用量未知也不假定失败免费。按卷提取错误标记、质量接受阈值或低正确率告警继续暂缓。

原M6单图上传、提取、提交入口已停用，HTTP返回410；历史记录、原图读取及既有作答复核保留。`VISION_*`只供旧服务历史兼容，不用于新版入口，无需为新流程继续配置。阶段六关闭或加载失败时明确提示，不再回退旧页面。本次新增0037核对清单迁移，启动后端时自动执行；已运行的开发容器也可用`docker compose exec backend alembic upgrade head`升级结构。

原M6单图流程使用手工裁剪后的答题区，相关历史方案见项目根目录 `11_数学教学Agent_M6模型接入与答题识别优化计划.md`。当前多页流程以阶段六S3记录为准：保持一份学生版试卷，题目之后另页开始带定位和身份标记的A4答题区；S3-C按答题页采集照片，S3-D已接入逐页视觉HTTP，真实照片效果、透视校正与自动裁剪仍待后续实现或验证。第一版使用一个主VLM；`PP-OCRv5`与`PP-FormulaNet_plus-M`仅在真实评测证明存在特定短板后作为可选adapter，不作为同时接入的前置依赖，当前不引入PaddlePaddle重型运行时。

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
