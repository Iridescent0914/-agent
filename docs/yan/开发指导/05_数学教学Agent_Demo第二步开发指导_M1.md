# 第二步开发指导：M1 工程骨架与基础数据

> 版本：v0.1  
> 日期：2026-09-23  
> 对应规划：`03_数学教学Agent_Demo开发步骤规划.md` 第 4 节（M1）。  
> 前置阶段：M0 已完成，实施结果见 `code/docs/04_M0_第一步工作总结与M1交接.md`。  
> 本文是 M1 的执行指导，不表示下文列出的工程、接口和页面已经实现。

## 1. M1 要完成什么

M1 的目标是把 M0 产出的有理数种子题放进一个可启动、可迁移、可重复导入、可通过 API 和网页查询的业务工程，并建立后续 M2–M4 所需的数据骨架。

完成后的最小效果是：

```text
Docker Compose 启动 PostgreSQL、FastAPI 和 React
→ Alembic 从空库创建全部基础表
→ 幂等导入 30 组有理数种子题和知识结构
→ 浏览器正确显示题干、选项、公式资源、答案和标签
→ 查看至少 5 名匿名虚拟学生
→ 查看这些学生可追溯的历史作答记录
→ 再次执行迁移和种子导入不会产生重复数据
```

M1 只建立题库、学生、历史作答和业务实体基础，不在这一阶段实现：

- 学生画像计算和能力雷达图；
- 个性化推荐算法；
- 教师换题、发布试卷的完整交互；
- 手动录入后的实时判分；
- OCR / VLM；
- AI 出题、RAG、Neo4j 或多 Agent；
- 其他章节的通用导入框架。

其中 `Assignment`、`PaperInstance`、`PaperInstanceItem`、`Submission`、`Attempt`、`AttemptRevision` 在 M1 建表，是为了让演示历史可追溯，并为后续阶段固定数据合同；它们的正式业务接口分别在 M3、M4 实现。

## 2. 开工前先确认 M0 输入

M1 只使用以下正式输入：

```text
code/data/seed/rational_questions.json
code/data/seed/rational_knowledge_tree.json
code/data/assets/rational/
```

以下文件继续保留用于治理和追溯，但不直接导入业务题库：

```text
code/data/interim/rational_all.json
code/data/audit/rational_import_audit.csv
code/data/audit/rational_m0_report.json
```

开工前在 `code/` 下重新执行：

```powershell
python .\scripts\build_rational_m0.py
python .\scripts\validate_m0.py
```

预期至少确认：

```text
all_questions: 139
seed_questions: 30
fill_blank: 20
choice: 10
seed_exam_points: 10
machine_ready: 30
validation_errors: 0
```

若 M0 验证失败，先修复数据或脚本，不把异常数据带入数据库后再补救。M1 导入器只消费正式种子，不重新解释 DOCX，也不修改 M0 的 JSON。

## 3. 技术选择与运行边界

本阶段采用以下固定组合：

```text
前端：React + TypeScript + Vite + Ant Design
后端：FastAPI + Pydantic + SQLAlchemy 2.x
数据库：PostgreSQL
迁移：Alembic
测试：pytest + FastAPI TestClient / httpx
本地编排：Docker Compose
```

ECharts 可以安装，但 M1 不要求完成画像图表。SymPy 可以先列入后端依赖或留到 M4；M1 不实现数学等价判定。

开发时不要使用 SQLite 代替 PostgreSQL。后续会依赖 JSONB、唯一约束、事务和并发控制，M1 就应在真实 PostgreSQL 上验证迁移与导入。

配置全部通过环境变量读取，提交 `.env.example`，不提交真实 `.env`。至少包括：

```text
APP_ENV
DATABASE_URL
POSTGRES_DB
POSTGRES_USER
POSTGRES_PASSWORD
CORS_ORIGINS
PUBLIC_ASSET_BASE_URL
```

Demo 数据只使用匿名编号和虚拟姓名，不放入真实学生信息。

## 4. 推荐目录结构

保留现有 M0 数据和脚本，在 `code/` 内扩展：

```text
code/
├── backend/
│   ├── alembic/
│   │   └── versions/
│   ├── app/
│   │   ├── api/
│   │   │   ├── router.py
│   │   │   └── routes/
│   │   │       ├── health.py
│   │   │       ├── questions.py
│   │   │       └── students.py
│   │   ├── core/
│   │   │   ├── config.py
│   │   │   └── database.py
│   │   ├── models/
│   │   ├── repositories/
│   │   ├── schemas/
│   │   ├── services/
│   │   │   └── seed_importer.py
│   │   └── main.py
│   ├── scripts/
│   │   ├── seed_database.py
│   │   └── verify_m1.py
│   ├── tests/
│   ├── alembic.ini
│   ├── Dockerfile
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── api/
│   │   ├── components/
│   │   ├── pages/
│   │   │   ├── QuestionListPage.tsx
│   │   │   ├── QuestionDetailPage.tsx
│   │   │   ├── StudentListPage.tsx
│   │   │   └── StudentDetailPage.tsx
│   │   ├── routes/
│   │   ├── types/
│   │   └── main.tsx
│   ├── Dockerfile
│   └── package.json
├── data/
│   ├── assets/rational/          # M0 原始资源，只读
│   ├── seed/                     # M0 正式种子，只读
│   └── web/rational/             # M1 转换后的 Web 资源
├── scripts/
│   ├── build_rational_m0.py
│   ├── validate_m0.py
│   └── convert_seed_assets.py
├── docker-compose.yml
├── .env.example
└── README.md
```

不要移动或重命名 M0 已生成的正式数据，以免破坏脚本和交接文档中的路径。后端容器以只读方式挂载 `data/seed/`，转换后的 `data/web/` 可以由后端静态服务或前端开发服务器访问。

## 5. 数据库设计

### 5.1 总体关系

```text
KnowledgePoint 1 ── N ExamPoint
Question N ── N ExamPoint（QuestionExamPoint）

Student 1 ── N PaperInstance
Assignment 1 ── N PaperInstance
PaperInstance 1 ── N PaperInstanceItem
PaperInstance 1 ── 0..1 Submission
PaperInstanceItem 1 ── 0..1 Attempt
Attempt 1 ── N AttemptRevision

Student N ── N ExamPoint（StudentMastery，M2 计算）
```

业务主键推荐使用 UUID。M0 中稳定的语义 ID 另设唯一字段：

- `Question.original_qid` 唯一；
- `KnowledgePoint.code` 唯一；
- `ExamPoint.code` 唯一；
- `PaperInstance.instance_code` 唯一且发布后不复用。

时间统一以带时区的 UTC 时间存储，前端按本地时区显示。JSON 内容使用 PostgreSQL `JSONB`，不要把答案对象转成不可查询的 Python 字符串。

### 5.2 题库与知识结构

#### KnowledgePoint

| 字段 | 类型 | 约束 / 说明 |
|---|---|---|
| id | UUID | 主键 |
| code | varchar | 唯一，保存 `math.g6.rational.add_sub` 等源 ID |
| name | varchar | 非空 |
| chapter_code | varchar | 当前为 `math.g6.rational` |
| chapter_name | varchar | 当前为“有理数” |
| section | varchar | 可空 |
| parent_id | UUID | 可空，自关联，当前没有明确父节点时不伪造 |

#### ExamPoint

| 字段 | 类型 | 约束 / 说明 |
|---|---|---|
| id | UUID | 主键 |
| code | varchar | 唯一，保存 M0 考点 ID |
| name | varchar | 非空 |
| knowledge_point_id | UUID | 外键，非空 |

#### Question

| 字段 | 类型 | 约束 / 说明 |
|---|---|---|
| id | UUID | 主键 |
| original_qid | varchar | 唯一、非空 |
| schema_version | varchar | 保存 M0 schema 版本 |
| chapter_code / chapter_name | varchar | 非空 |
| section | varchar | 非空 |
| question_type | varchar | `choice` / `fill_blank` |
| difficulty | smallint | 1–5 检查约束 |
| main_knowledge_point_id | UUID | 外键，非空 |
| question_text | text | 非空 |
| options | JSONB | 选择题为结构化选项，填空题可空 |
| correct_answer | JSONB | 非空，原样保留单答案或 group 结构 |
| reference_answer_raw | text | 非空，便于人工核对 |
| answer_match_type | varchar | 非空 |
| answer_normalization_rule | JSONB | 非空 |
| solution | text | 可空 |
| wrong_analysis | text | 可空 |
| estimated_score_rate | varchar | 可空，M1 不据此计算 |
| has_image / has_equation | boolean | 非空 |
| assets | JSONB | 原始资源和 Web 资源映射 |
| source_file | text | 非空 |
| source_question_number | integer | 非空 |
| source_review_status | varchar | 当前正式种子应为 `reviewed` |
| grading_readiness | varchar | 当前正式种子应为 `machine_ready` |
| created_at / updated_at | timestamptz | 非空 |

#### QuestionExamPoint

| 字段 | 类型 | 约束 / 说明 |
|---|---|---|
| question_id | UUID | 联合主键 / 外键 |
| exam_point_id | UUID | 联合主键 / 外键 |
| relation_type | varchar | `main` 或 `related` |

约束：一题只能有一个 `main` 考点；主考点也写入关联表，避免后续维护两套互相矛盾的数据。关联考点为空是合法情况。

知识树文件可能包含首批 30 题暂未引用的考点。M1 可以导入整个有理数知识树，但题库筛选和统计必须区分“知识树中存在”与“当前种子题已覆盖”。验收中的“覆盖 10 个考点”按种子题实际关联计算。

### 5.3 学生和画像占位表

#### Student

| 字段 | 类型 | 约束 / 说明 |
|---|---|---|
| id | UUID | 主键 |
| student_code | varchar | 唯一，如 `S001` |
| display_name | varchar | 使用虚拟名称 |
| class_name | varchar | 如“六年级 Demo 班” |
| is_demo | boolean | M1 固定为 true |
| created_at | timestamptz | 非空 |

#### StudentMastery

| 字段 | 类型 | 约束 / 说明 |
|---|---|---|
| student_id | UUID | 联合主键 / 外键 |
| exam_point_id | UUID | 联合主键 / 外键 |
| mastery | numeric | 0–1；由 M2 重放作答后计算 |
| confidence_level | varchar | `low` / `medium` / `high` |
| attempt_count / correct_count / recent_wrong_count | integer | 非负 |
| last_practiced_at / updated_at | timestamptz | 可空 / 非空 |

M1 只创建 `StudentMastery` 表，不把手写画像值作为学生差异的来源。可以保持为空，或在 M2 实现重放算法后由历史 Attempt 生成。前端 M1 的学生详情展示历史作答，不展示伪造的画像结论。

### 5.4 为后续闭环建立的试卷与作答实体

以下字段应在 M1 migration 中固定，具体换题、发布和判分服务后续实现。

#### Assignment

```text
id, title, chapter_code, teaching_scope(JSONB), assignment_type,
created_at
```

#### PaperInstance

```text
id, assignment_id, student_id, version_no, status,
instance_code, supersedes_instance_id, created_at, published_at
```

约束：

- `(assignment_id, student_id, version_no)` 唯一；
- `status` 仅允许 `draft`、`published`；
- `instance_code` 全局唯一；
- published 不可修改的规则最终由 M3 服务和数据库触发策略共同保证。

#### PaperInstanceItem

```text
id, paper_instance_id, question_id, order_no,
question_snapshot(JSONB), options_snapshot(JSONB),
answer_snapshot(JSONB), answer_rule_snapshot(JSONB),
exam_point_snapshot(JSONB), answer_region(JSONB), created_at
```

约束：

- `(paper_instance_id, order_no)` 唯一；
- 快照字段非空；
- 历史批改读取快照，不回读当前 Question 的答案。

#### Submission

```text
id, student_id, paper_instance_id, source, idempotency_key,
attachments(JSONB), submitted_at
```

约束：

- `paper_instance_id` 唯一，一份试卷只有一个逻辑 Submission；
- `source` 为 `manual` 或 `image`；
- Submission 学生必须与 PaperInstance 所属学生一致，该规则由服务层验证。

#### Attempt

```text
id, submission_id, student_id, paper_instance_item_id, question_id,
student_answer(JSONB), normalized_answer(JSONB),
grade_status, is_correct, ocr_confidence, recognition_status,
manual_override, revision_no, answered_at, reviewed_at
```

约束：

- `paper_instance_item_id` 唯一；
- `is_correct` 可空，未知不能写成 false；
- `needs_review` 时 `is_correct` 必须为空；
- `grade_status` 为 `auto_confirmed`、`needs_review`、`teacher_confirmed`。

#### AttemptRevision

```text
id, attempt_id, revision_no, before_state(JSONB), after_state(JSONB),
operator_type, reason, created_at
```

约束 `(attempt_id, revision_no)` 唯一。M1 演示历史至少保存初始 revision；M4 再实现教师修正和并发版本检查。

### 5.5 migration 拆分建议

不要把全部表、索引和演示数据塞进一个 migration。建议：

```text
0001_create_knowledge_and_question_tables
0002_create_student_and_mastery_tables
0003_create_assignment_paper_submission_attempt_tables
0004_add_indexes_and_constraints
```

Migration 只定义 schema，不插入会频繁变化的题库和演示数据。题库与虚拟学生统一由独立 seed 命令导入。

至少建立以下查询索引：

- `questions(original_qid)` 唯一索引；
- `questions(chapter_code, question_type, difficulty)`；
- `exam_points(code)` 唯一索引；
- `question_exam_points(exam_point_id, question_id)`；
- `students(student_code)` 唯一索引；
- `attempts(student_id, answered_at)`；
- `paper_instances(instance_code)` 唯一索引。

## 6. 幂等种子导入

### 6.1 一个命令完成导入

实现后约定在 `code/backend/` 执行：

```powershell
python -m scripts.seed_database
```

该命令按固定顺序执行：

```text
读取并校验两个 M0 JSON
→ upsert KnowledgePoint
→ upsert ExamPoint
→ upsert Question
→ 重建该题的 QuestionExamPoint 关联
→ 导入 5 名虚拟学生
→ 导入可追溯的演示历史
→ 输出导入前后统计和异常
```

### 6.2 upsert 的身份键

使用以下稳定键判断同一记录：

```text
KnowledgePoint.code
ExamPoint.code
Question.original_qid
Student.student_code
Assignment 的预设 seed_key 或固定业务标识
PaperInstance.instance_code
```

不要按数据库 UUID 判断种子是否已存在。第二次执行导入时，记录总数应保持不变；源字段变化时更新允许更新的题库字段，并在日志中输出 changed 数量。

每次导入必须在事务中运行。任意正式题缺主考点、标答、判分类型或引用资源时，整次题库导入回滚，不能留下半套数据。

建议输出：

```text
knowledge_points: inserted 1, updated 0, unchanged ...
exam_points: inserted ..., updated ..., unchanged ...
questions: inserted 30, updated 0, unchanged 0
students: inserted 5, updated 0, unchanged 0
attempts: inserted ..., updated 0, unchanged 0
```

### 6.3 JSON 字段映射

M0 中以下结构应原样保存，不要在 M1 导入时压平成逗号字符串：

```text
options
correct_answer
answer_normalization_rule
assets
```

`correct_answer.kind = group` 时，保留每个 item 的：

```text
item_id
answer
answer_match_type
reference_answer_raw
```

这会直接影响 M4 的逐空判分。导入阶段不要因为 M1 尚不判分就丢掉这些字段。

## 7. 处理 112 个种子题 WMF 引用

M0 交接已确认：30 组种子题中有 18 组引用公式资源，共 112 个 WMF 引用。标准答案已是文本结构，资源转换只影响 Web 展示，不影响题目是否可判分。

实现 `code/scripts/convert_seed_assets.py`，只处理种子题实际引用的资源：

```text
扫描 rational_questions.json 中的 assets 和题干占位符
→ 验证源资源位于 data/assets/rational
→ 计算源文件 SHA-256
→ 转成 SVG；不能稳定转 SVG 时转 PNG
→ 写入 data/web/rational
→ 生成 data/web/rational/asset_manifest.json
```

manifest 至少包含：

```json
{
  "source_path": "assets/rational/example.wmf",
  "source_sha256": "...",
  "web_path": "rational/example.svg",
  "format": "svg",
  "status": "converted"
}
```

转换工具可选择本机可重复调用的 LibreOffice、Inkscape 或经过验证的 WMF 转换库，但最终必须由脚本统一调度，不能人工逐张另存。第一次先用 3–5 个资源验证负号、分数线、上下标和括号，再批量转换。

要求：

- 同一源哈希已成功转换时跳过；
- 输出文件名稳定且不会互相覆盖；
- 转换失败写入 manifest 并使验收脚本失败；
- 不覆盖或删除 M0 原始 WMF；
- 随机抽查之外，至少自动检查所有引用都有输出文件且非空；
- 题库页面不能把 `[ASSET:...]` 原样展示给用户。

前端渲染题干时，把文本和资源占位符解析成有序片段并分别渲染。不要把题干直接交给 `dangerouslySetInnerHTML`。图片应有替代文本、最大宽度和加载失败状态。

## 8. 虚拟学生与可解释历史

至少建立：

| 编号 | 演示特征 | 历史数据应体现 |
|---|---|---|
| S001 | 概念基础较弱 | 有理数概念、数轴相关错误较多 |
| S002 | 计算较弱 | 加减、乘除和符号判断错误较多 |
| S003 | 基础稳定 | 中低难度题多数正确，覆盖较均衡 |
| S004 | 整体较强 | 中高难度题正确率相对较高 |
| S005 | 旧知识需要回顾 | 较早记录较好，近期特定考点出现错误 |

不能只给这 5 人写不同的 `StudentMastery.mastery` 数值。演示历史必须形成以下完整证据链：

```text
历史 Assignment
→ 每名学生的 published PaperInstance
→ PaperInstanceItem 题目与答案快照
→ manual Submission
→ teacher_confirmed Attempt
→ AttemptRevision(revision_no = 1)
```

这些 Attempt 可以由 seed 脚本根据预先写好的演示答案生成，但答案和 `is_correct` 必须自洽。M1 暂不实现判分引擎时，可在种子文件中明确保存预期结论；同时写一个一致性检查，至少保证：

- 正确记录的学生答案与标准答案按简单规则一致；
- 错误记录不能意外等于标准答案；
- Attempt、Submission、PaperInstance 的 student_id 一致；
- Attempt 关联的是 PaperInstanceItem，而不只是 Question；
- 每条历史都有稳定的 `answered_at`，能表现先后变化；
- 不把 `needs_review` 数据伪造成已确认画像证据。

演示历史使用固定时间、固定随机种子或明确数据文件，保证重复导入得到相同结果，不能每次随机生成另一套学生表现。

## 9. M1 最小后端 API

统一前缀建议为 `/api/v1`。

### 9.1 健康检查

```text
GET /api/v1/health
GET /api/v1/ready
```

- `health` 只表示进程存活；
- `ready` 检查数据库连接和 migration 版本是否可用；
- 不把数据库密码、连接串等配置返回给前端。

### 9.2 题库

```text
GET /api/v1/questions
GET /api/v1/questions/{question_id}
```

列表至少支持：

```text
page
page_size（设置上限，例如 100）
question_type
difficulty
knowledge_point_code
exam_point_code
keyword
```

列表响应包含总数、分页信息和摘要，不返回完整标准答案的大段原始内容。详情接口返回题干、选项、结构化答案、难度、知识点、主 / 关联考点、易错分析、来源和资源 URL。

M1 不开放 `POST /questions/import` 给前端。导入是受控运维命令，避免页面重复触发。题库编辑也留到确有需求时实现。

### 9.3 学生

```text
GET /api/v1/students
GET /api/v1/students/{student_id}
GET /api/v1/students/{student_id}/attempts
```

学生详情至少返回基本信息和历史作答摘要。历史接口支持分页，并返回：

```text
作答时间
题目 original_qid 和摘要
主考点
难度
学生答案
当前最终结论
grade_status
paper_instance_id / paper_instance_item_id
```

M1 不返回已经“计算好”的薄弱点结论；那是 M2 的职责。页面可以客观显示历史正确 / 错误记录。

### 9.4 通用 API 约定

- Pydantic 响应模型禁止意外暴露 ORM 全字段；
- 404、参数校验错误和数据库异常使用统一错误结构；
- 列表有稳定排序，例如 `(created_at, id)` 或 `(original_qid, id)`；
- CORS 只允许配置中的前端地址；
- 所有查询避免 N+1，主考点和资源按需预加载；
- OpenAPI `/docs` 可用于 M1 联调。

## 10. M1 前端页面

M1 只做四个页面：

### 10.1 题库列表

展示：

```text
original_qid
题型
难度
知识点
主考点
题干摘要
是否含公式 / 图片
```

支持题型、难度、考点筛选和关键词查询；筛选条件写入 URL query，刷新页面后仍可复现。

### 10.2 题目详情

展示完整题干、选项、公式 / 图片、结构化标准答案、判分类型、易错分析、来源信息。group 答案按 `item_id` 逐项展示，不能直接显示 `[object Object]`。

### 10.3 学生列表

展示学生编号、虚拟名称、班级、历史作答数和最近作答时间。不要在 M1 用没有计算依据的“能力分数”装饰页面。

### 10.4 学生详情

展示学生信息和历史作答表，可以按正确 / 错误、考点筛选。明确标注这些是演示数据。M2 再在此页面增加掌握度、置信度和趋势图。

前端必须有加载中、空数据、请求失败和资源加载失败状态。页面先保证信息正确与可核对，不在 M1 花时间制作复杂视觉效果。

## 11. Docker Compose 与启动流程

Compose 至少包含：

```text
postgres
backend
frontend
```

启动依赖顺序通过数据库 healthcheck 和后端启动脚本控制，不用固定 sleep。后端启动时可以执行 `alembic upgrade head`，但种子导入应是显式的一次性命令，避免每次 Web 进程启动都悄悄改业务数据。

建议启动流程：

```powershell
cd code
Copy-Item .env.example .env
docker compose up -d --build
docker compose exec backend alembic upgrade head
docker compose exec backend python -m scripts.seed_database
docker compose exec backend python -m scripts.verify_m1
```

随后检查：

```text
前端：http://localhost:5173（或 compose 中约定端口）
后端文档：http://localhost:8000/docs
健康检查：http://localhost:8000/api/v1/health
```

README 要同时写出 Docker 运行方式和必要的本机调试方式。命令、端口和目录一旦确定，应以仓库实际配置为准，不能让 README 与 Compose 漂移。

## 12. 实施顺序

### 批次 A：工程能启动

1. 创建 backend、frontend 和 Compose 骨架。
2. 实现配置读取、数据库会话、FastAPI 主入口和健康检查。
3. 创建 React 路由和最小布局。
4. 验证三个容器可启动，前端可访问后端健康接口。

结束条件：空业务工程可以一条 Compose 命令启动，配置中无硬编码密码。

### 批次 B：数据库合同固定

1. 实现全部 M1 ORM 模型。
2. 生成并人工检查 Alembic migrations。
3. 从空库执行到 head，再 downgrade / upgrade 验证迁移基本可逆性。
4. 检查唯一约束、外键、枚举 / check constraint 和索引。

结束条件：空库可稳定迁移，错误关联和重复键会被数据库拒绝。

### 批次 C：题库导入和资源展示

1. 实现种子 JSON 的 Pydantic 输入模型或显式校验器。
2. 导入知识点、考点、题目和关联。
3. 实现 WMF 批量转换与 asset manifest。
4. 实现题库 API、列表页和详情页。
5. 第二次执行 seed，核对无重复。

结束条件：30 组题全部可查询，含资源的题目在浏览器可读。

### 批次 D：学生与演示历史

1. 导入 5 名虚拟学生。
2. 建立固定历史 Assignment 和各学生的发布试卷快照。
3. 导入 Submission、Attempt 和初始 AttemptRevision。
4. 实现学生 API、列表页和详情页。
5. 检查每条历史可沿外键追溯到学生实际试卷和题目快照。

结束条件：5 名学生呈现不同且可解释的客观历史，为 M2 画像计算提供输入。

### 批次 E：自动验收与交接

1. 编写 `verify_m1.py`。
2. 完成后端关键集成测试和前端构建检查。
3. 从全新数据库完整走一遍迁移、导入、查询。
4. 更新 `code/README.md`，新增 M1 工作总结与 M2 交接文档。

结束条件：其他开发者按 README 可以复现 M1，不依赖本机残留数据库或手工改表。

## 13. 测试与自动验证

M1 的测试重点是数据合同和可复现性，不需要为简单 UI 样式编写测试。

### 13.1 后端必须验证

- 空库 `alembic upgrade head` 成功；
- 30 组种子题全部导入；
- 题型分布为填空 20 组、选择 10 组；
- 30 组题均为 `machine_ready` 且来源状态为 `reviewed`；
- 种子题实际覆盖 10 个主考点；
- `original_qid`、知识点 code、考点 code 无重复；
- 每题恰有一个主考点关联；
- 所有题目引用资源都有 Web 文件；
- 第二次导入后各表数量不增加；
- 至少 5 名虚拟学生，且均有可追溯历史；
- Attempt 的学生、Submission、PaperInstance 所属一致；
- 题库筛选、分页和详情接口返回正确；
- 不存在的题目和学生返回 404。

### 13.2 前端必须验证

- TypeScript 类型检查通过；
- production build 通过；
- 四个页面可通过路由直接访问；
- 筛选、分页和错误状态可用；
- 选择题选项、多空答案和资源占位符正确渲染；
- 后端不可用时页面给出明确错误，不无限加载或白屏。

### 13.3 `verify_m1.py` 建议输出

```text
schema_at_head: true
questions: 30
fill_blank: 20
choice: 10
seed_exam_points: 10
questions_with_assets: 18
missing_web_assets: 0
students: 5
students_with_attempts: 5
orphan_records: 0
duplicate_seed_keys: 0
validation_errors: 0
```

`questions_with_assets: 18` 来自当前 M0 交接；若重新构建 M0 后数据发生变化，验收脚本应读取正式种子计算并在交接中说明差异，而不是为了匹配旧数字硬编码结果。

## 14. M1 验收步骤

建议在干净 Demo 数据库上执行最终验收。若使用 `docker compose down -v` 清理数据库卷，应先确认卷中只有可重建的 Demo 数据；该命令会删除本地 Compose 数据卷。

```text
1. 从空数据库启动三个服务。
2. 执行全部 Alembic migration。
3. 执行资源转换和 seed 导入。
4. 再执行一次 seed，确认幂等。
5. 运行 verify_m1.py 和后端测试。
6. 运行前端类型检查和 production build。
7. 浏览器抽查无资源题、含公式题、选择题、多空填空题。
8. 查看 5 名学生及其历史作答证据链。
9. 按 README 在另一终端重新复现关键命令。
```

通过标准：

- 前端、后端、PostgreSQL 可本地一键启动；
- migration 能从空库执行到最新版本；
- 30 组正式种子题完整、幂等导入；
- 题库可按题型、难度、知识点 / 考点查询；
- 题干中的公式和图片资源可正确显示；
- 至少 5 名匿名虚拟学生拥有不同、可回溯的历史 Attempt；
- 学生与题目详情可通过 API 和页面查看；
- 自动验证为 0 个错误；
- 不依赖 AI、OCR 或外部模型服务。

## 15. M1 完成后应提交的产物

```text
code/backend/                         FastAPI、ORM、迁移、API、测试
code/frontend/                        React 页面与类型
code/docker-compose.yml               本地编排
code/.env.example                     无秘密的配置模板
code/scripts/convert_seed_assets.py   种子资源转换
code/data/web/rational/               Web 资源及 manifest
code/backend/scripts/seed_database.py 幂等导入
code/backend/scripts/verify_m1.py      M1 自动验收
code/README.md                         实际启动和验证命令
code/docs/05_M1_工作总结与M2交接.md    实施结果、统计、遗留问题
```

工作总结应记录实际依赖版本、migration head、导入数量、Web 资源转换成功率、学生历史规模、测试结果和未解决问题。不要把指导文档中的计划数字直接复制成“已完成结果”。

## 16. 常见返工点

1. **直接把 `correct_answer` 存成字符串。** 这会破坏多空题结构，M4 需要返工数据库和导入器。
2. **虚拟学生只写 mastery，没有 Attempt。** M2 无法解释画像，也无法验证重算。
3. **Attempt 只关联 Question。** 以后题库修改后无法按学生实际试卷快照批改。
4. **每次启动自动重复插入 seed。** 数据数量不断增长，测试结果失真。
5. **把全部知识树考点当作已覆盖考点。** 当前 30 组种子题实际只覆盖其中一部分。
6. **浏览器直接显示 WMF 或 `[ASSET:...]`。** 多数浏览器不能稳定显示 WMF，占位符也不是用户内容。
7. **为赶进度使用 SQLite。** M2–M4 再切 PostgreSQL 时会重新处理 JSON、约束和事务差异。
8. **M1 提前做推荐、OCR 或复杂图表。** 基础表、导入和追溯链不稳定时，这些功能只会放大返工。
9. **迁移里混入演示业务数据。** 数据变化会污染 schema 迁移历史，应使用独立 seed 命令。
10. **只在已有数据库上测试。** 必须从空库复现，才能证明 migration 和 README 可靠。

## 17. M1 完成检查表

- [ ] M0 校验仍为 0 个错误。
- [ ] backend、frontend、postgres 可通过 Docker Compose 启动。
- [ ] Alembic 可从空库迁移到 head。
- [ ] 题库、知识结构、学生和作答基础实体已建表并有约束。
- [ ] 30 组有理数种子题完整导入。
- [ ] 重复执行 seed 不增加重复记录。
- [ ] 112 个种子 WMF 引用均有转换结果或明确失败报告。
- [ ] 题库列表、详情、筛选和分页可用。
- [ ] 至少 5 名虚拟学生已导入。
- [ ] 每名学生的差异由可追溯 Attempt 支撑。
- [ ] 学生列表、详情和历史作答页面可用。
- [ ] API 错误、空数据和前端加载失败状态可见。
- [ ] 后端测试、自动验收、前端类型检查和构建通过。
- [ ] README 可以指导他人从空环境复现。
- [ ] 已形成 M1 工作总结与 M2 交接文档。

满足以上条件后进入 M2：从确认后的历史 Attempt 重放得到 `StudentMastery`，展示证据量、考点掌握情况和初步弱点，并为 M3 的规则推荐准备可解释输入。

## 18. 现在最先做的一件事

先完成批次 A，并提交一个最小但真实的启动链：

```text
PostgreSQL healthcheck 正常
→ FastAPI 能连接数据库并返回 /api/v1/ready
→ React 能调用 /api/v1/health 并显示服务状态
→ Alembic 能在空库创建第一张表
```

这条链稳定后，再开始一次性定义完整实体和导入 30 组题。不要先写页面假数据，也不要先做学生画像算法。
