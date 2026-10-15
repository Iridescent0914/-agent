# 数学教学 Agent Demo 第四步开发指导：M3 个性化组卷、教师调整与发布

> 对应《03_数学教学Agent_Demo开发步骤规划.md》第 6 节。  
> 当前基线：M0、M1、M2 已完成；本文依据现有代码和 `code/docs/06_M2_工作总结与M3交接.md` 编写。  
> 文档日期：2026-09-26。

---

## 1. M3 要交付什么

M3 要把 M2 的“只读推荐预览”变成教师可以实际确认和发布的学生试卷。完成后应跑通：

```text
教师填写一次作业设置
→ 同一时刻为 5 名虚拟学生读取画像
→ 复用 M2 推荐服务生成各自的 draft PaperInstance
→ 保存生成参数、推荐理由、证据和候选缺口
→ 教师逐份查看、换题、删题、调序
→ 教师发布
→ 生成全局唯一 instance_code
→ 冻结该学生最终题目、顺序、标答和判分规则快照
→ 旧编号和新编号都能定位各自版本
```

最终用户看到的核心结果不是“推荐列表”，而是：一次 `Assignment` 下，每名学生拥有一份独立、可编辑、可发布、发布后可追溯的 `PaperInstance`。

### 1.1 本阶段必须完成

- 创建一次逻辑作业并明确选择学生；当前没有班级实体，班级选择在前端按 `Student.class_name` 筛选后提交明确的 `student_ids`。
- 使用 M2 的 `preview` / `select_recommendations`，为每名学生生成独立草稿；不复制第二套选题算法。
- 所有学生使用同一个带时区的 `as_of`，保存规则版本、完整参数、每题分项得分、理由、证据引用和缺口诊断。
- 从当前 `Question` 构造 `PaperInstanceItem` 快照；教师看到的草稿内容就是之后发布的内容，发布时不再偷偷刷新题库字段。
- 只允许修改 draft：换题、删题、调序。
- 发布时校验试卷完整性，生成唯一且不复用的 `instance_code`，设置 `published_at`，随后由数据库触发器保证不可修改。
- 发布后要改题时，复制为新 draft，`version_no + 1`，并用 `supersedes_instance_id` 指向旧版；新版本发布时获得新编号。
- 能按 UUID 查看试卷，也能按 `instance_code` 精确查找已发布版本；旧编号绝不重定向到最新版。
- 完成作业列表、创建/预检页、学生试卷编辑页和发布确认交互。
- 增加后端单测、接口集成测试、数据库约束测试、前端端到端测试和只读验收脚本。

### 1.2 阶段边界

| M3 完成 | 暂不在 M3 完成 |
|---|---|
| Assignment 创建与学生选择 | M4 手工答案录入和规则判分 |
| 多学生推荐预检与正式生成 | M4 Submission / Attempt 新建与改判事务 |
| 草稿换题、删题、调序 | M4 根据新作答重算画像 |
| 单份和批量发布、实例码 | M6 打印正文、统一答题区和二维码 |
| 发布后复制新版本 | M6 图片上传与 VLM 答案识别 |
| 推荐和教师调整审计 | 新题生成、LLM 解题或 AI 自主决策 |

M3 不实现 `/print`，也不创建新的 `Submission` 或 `Attempt`。页面可以展示学生预览，但不应伪装成已经生成可打印答题卡。

---

## 2. 当前基础与开工检查

### 2.1 已有成果

当前代码已经提供：

- PostgreSQL、SQLAlchemy、Alembic、FastAPI、React、Ant Design 和 Playwright 工程；
- 30 组已审核且 `machine_ready` 的有理数题、54 个考点、5 名虚拟学生；
- 3 个历史种子 Assignment、15 份已发布历史试卷、150 个快照题和 150 条确认作答；
- `Assignment`、`PaperInstance`、`PaperInstanceItem` 的基础表；
- `(assignment_id, student_id, version_no)`、`instance_code`、题序等数据库唯一约束；
- `0004_add_published_snapshot_guards.py` 中的数据库触发器：已发布试卷及题目快照禁止增删改；
- 可重放画像、证据详情和规则推荐预览；
- 推荐规则 `m2-v1` 的范围、题型、难度、近期去重、考点占比和候选缺口逻辑；
- 学生详情页中的推荐参数表单、理由、证据和缺口展示。

不要重建工程、重导题库或另做一套画像。M3 是在这些稳定接口上增加“持久化与工作流”。

### 2.2 优先阅读的现有文件

```text
code/docs/06_M2_工作总结与M3交接.md
code/backend/app/models/entities.py
code/backend/alembic/versions/0003_create_workflow_tables.py
code/backend/alembic/versions/0004_add_published_snapshot_guards.py
code/backend/app/services/recommendations.py
code/backend/app/services/student_profile.py
code/backend/app/schemas/recommendations.py
code/backend/app/api/routes/profile.py
code/backend/app/services/seed_importer.py
code/backend/tests/test_recommendations.py
code/backend/tests/test_published_snapshot_guard.py
code/frontend/src/components/RecommendationPreview.tsx
code/frontend/src/pages/StudentDetailPage.tsx
code/frontend/src/api/client.ts
```

### 2.3 开工基线

先确认数据库和 M2 没有被破坏：

```powershell
Set-Location 'D:\desktop\math-agent\code\backend'
$env:DATABASE_URL = 'postgresql+psycopg://postgres@localhost:55432/math_agent'

& '..\.conda-env\python.exe' -m scripts.verify_m1
& '..\.conda-env\python.exe' -m scripts.verify_m2
& '..\.conda-env\python.exe' -m pytest -q
```

前端基线：

```powershell
Set-Location 'D:\desktop\math-agent\code\frontend'
npm run build
npm run test:e2e
```

如基线失败，先区分数据库未启动、迁移版本不一致和真实回归，不要用删除数据库重新导入掩盖问题。

---

## 3. 先固定 M3 的六条业务不变量

### 3.1 Assignment 与学生试卷不是同一个概念

`Assignment` 表示一次逻辑作业，`PaperInstance` 表示某名学生实际拿到的一份试卷。同一 Assignment 下允许题目相同，也允许不同；不要求为了“看起来个性化”而强行让 5 份试卷两两不同。

```text
Assignment A
├─ S001 → PaperInstance v1
├─ S002 → PaperInstance v1
├─ S003 → PaperInstance v1
├─ S004 → PaperInstance v1
└─ S005 → PaperInstance v1
```

### 3.2 推荐预览只读，正式生成才写库

多学生预检和现有学生推荐预览都不能增加 Assignment 或 PaperInstance。只有教师点击“生成草稿”后，才在一个数据库事务中写入：

```text
1 个 Assignment
N 个 PaperInstance(draft)
每份试卷对应的 PaperInstanceItem
推荐与缺口审计
```

任一学生发生参数错误、证据异常或快照构造失败时，整次生成回滚，不能留下半个班的试卷。

### 3.3 一个批次只取一次 as_of

在请求入口生成一次 `datetime.now(timezone.utc)`，所有学生共用这个值。不能在循环内为每名学生重新取时间，否则“距上次练习时间”和推荐审计的基准不一致。

同一次正式生成应保存：

```text
rule_version
as_of
完整 RecommendationRequest
weights
每名学生的 RecommendationOut
```

### 3.4 快照在草稿生成或换题时创建

创建某道草稿题时，从当前题库复制题干、选项、资源、答案、判分规则和考点。发布只是把教师已经看过的草稿冻结，不能重新从 `Question` 刷新内容。

因此：

- 修改当前题库，不影响已经生成的草稿和已发布试卷；
- 替换题目时，新题按替换当时的题库内容创建新快照；
- 从已发布版本创建修订版时，先深拷贝旧版快照，不回读当前题库；
- API 展示试卷时读 `PaperInstanceItem` 快照，不能 join 当前 `Question` 后覆盖历史内容。

### 3.5 发布是不可逆状态迁移

允许的主路径只有：

```text
draft → published
```

不提供 `published → draft`，也不通过管理员接口直接修改 published。发布后需要改题时创建新实例，旧版、旧编号和旧快照继续保留。

### 3.6 版本号与草稿并发修订号必须分开

- `version_no`：学生实际试卷的业务版本，发布后改题才增加。
- `edit_revision`：同一个 draft 在页面编辑时的乐观锁，从 1 开始，每次换题、删题或调序加 1。

前端提交 `expected_edit_revision`。不相等返回 `409 Conflict` 并要求刷新，避免两个浏览器标签互相覆盖。不要用 `version_no` 充当并发锁。

---

## 4. 数据库增量设计

### 4.1 新增 0005，不能改写历史迁移

新增：

```text
backend/alembic/versions/0005_add_paper_generation_audit.py
```

建议字段如下。

#### Assignment

| 字段 | 类型 | 用途 |
|---|---|---|
| `idempotency_key` | `varchar(100)`, nullable, unique | 防止教师重复点击或网络重试创建重复作业 |
| `generation_config` | `jsonb`, nullable | M3 的规范化学生范围、推荐参数和共同 `as_of`；历史种子可为空 |

`teaching_scope` 只保留便于教师阅读的业务范围，如新知识和回顾考点；不要把完整推荐响应临时塞进这里。

#### PaperInstance

| 字段 | 类型 | 用途 |
|---|---|---|
| `recommendation_audit` | `jsonb`, nullable | 该学生生成时的完整 `RecommendationOut`；历史种子可为空 |
| `edit_history` | `jsonb`, not null, default `[]` | 换题、删题、调序、缺口确认等草稿事件 |
| `edit_revision` | `integer`, not null, default `1` | 草稿乐观锁 |

增加 `edit_revision > 0` 检查。`recommendation_audit` 保留最初生成结果，即使教师后来删掉某题，也仍能解释系统最初为何推荐。

#### PaperInstanceItem

| 字段 | 类型 | 用途 |
|---|---|---|
| `selection_audit` | `jsonb`, nullable | 当前题的来源、bucket、得分、理由、证据；历史种子可为空 |

M3 新生成的题必须非空；迁移层允许旧 M1 历史为空，避免伪造历史推荐理由。

### 4.2 推荐审计 JSON 的稳定结构

不要直接依赖 Pydantic 默认序列化结果永远不变。显式加入审计 schema 版本：

```json
{
  "schema_version": "m3-recommendation-audit-v1",
  "rule_version": "m2-v1",
  "as_of": "2026-09-26T12:00:00Z",
  "parameters": {},
  "weights": {},
  "status": "ready",
  "requested_count": 10,
  "selected_count": 10,
  "shortfall": 0,
  "diagnostics": [],
  "initial_items": []
}
```

每个 `selection_audit` 至少保存：

```text
schema_version
source                    recommendation / teacher_replacement / version_copy
bucket                    new / review
strategy                  personalized / diagnostic
score、components、contributions、target_difficulty
reason_codes、reason_text
evidence[{attempt_id, revision_no}]
selected_at
replaces（换题时记录旧 PaperInstanceItem / Question）
```

不能只存一段中文理由。分项值和证据修订号是之后复现解释的依据。

### 4.3 教师编辑历史

`edit_history` 中的事件建议统一为：

```json
{
  "event_id": "uuid",
  "type": "replace_item",
  "at": "2026-09-26T12:10:00Z",
  "actor": "demo_teacher",
  "before_revision": 2,
  "after_revision": 3,
  "details": {}
}
```

Demo 尚无登录系统，`actor` 可以固定为 `demo_teacher`，但不能伪装成真实教师账户。后续接认证时再改为用户 ID。

### 4.4 发布完整性约束

数据库现有 `0004` 已能阻止发布后的 UPDATE / DELETE，以及已发布试卷题目的 INSERT / UPDATE / DELETE。M3 需要在服务层额外检查：

- draft 才能编辑或发布；
- 至少有 1 道题；
- `order_no` 必须正好是 `1..N`，无重复、无缺口；
- 同一份试卷不能出现重复 `question_id`；
- 快照所需字段均存在；
- `PaperInstance.student_id` 属于本 Assignment 的生成范围；
- 发布时 `instance_code` 和 `published_at` 同时写入；
- 候选缺口或教师删题造成题量不足时，必须显式确认，不能静默发布。

可在 0005 增加“published 必须具有非空编号和发布时间”的检查，但不要贸然增加“draft 的编号必须为空”的数据库检查：现有种子导入代码曾在切换 published 前暂存编号。M3 新业务服务应坚持 draft 不分配编号；如要采用更严格数据库检查，必须同时修改种子导入并完成全量回归。

### 4.5 实例码格式

建议使用易抄写、去除易混字符的随机短码，例如：

```text
RAT-7K3M-9QXF
```

字符集排除 `0/O`、`1/I/L`。编号必须：

- 发布时生成；
- 全局唯一且永不复用；
- 不嵌入学生姓名等隐私；
- 数据库唯一约束冲突时重试生成；
- 新版本生成新编号；旧编号仍查旧实例。

不要用 `count + 1`，也不要根据学生 ID 直接推导。

---

## 5. 后端职责拆分

建议新增：

```text
backend/app/services/paper_snapshots.py
backend/app/services/paper_generation.py
backend/app/services/paper_editing.py
backend/app/services/paper_publication.py
backend/app/schemas/assignments.py
backend/app/schemas/papers.py
backend/app/api/routes/assignments.py
backend/app/api/routes/papers.py
```

职责如下：

| 模块 | 职责 |
|---|---|
| `recommendations.py` | 候选加载、打分、约束选择、替换候选排序；仍是唯一推荐规则来源 |
| `paper_snapshots.py` | 从 Question 构造完整快照、从旧 Item 深拷贝快照、校验快照 |
| `paper_generation.py` | 多学生预检、Assignment 与草稿的原子创建、审计保存 |
| `paper_editing.py` | draft 校验、换题、删题、两阶段调序、编辑历史和乐观锁 |
| `paper_publication.py` | 发布前校验、短码生成、单份/批量发布、复制新版本、按码解析 |
| 路由 | 参数和 HTTP 错误映射；不在路由里重新写业务算法 |

服务函数只 `flush()`，不在学生循环或题目循环中 `commit()`；请求级事务统一提交，异常统一回滚。

### 5.1 提取共享推荐核心

当前 `select_recommendations` 已经稳定。为了“换一题”能给出同口径备选，不要在换题路由中手写一个按难度排序的查询。可把现有逻辑小步重构为：

```text
load_candidates(...)
score/rank_candidates(...)
select_with_constraints(...)
preview(...)
replacement_candidates(...)
```

现有 `preview` 的请求和响应必须保持兼容，M2 测试继续通过。替换候选在相同 bucket 下：

- 继续遵守章节、审核状态、可判分状态、教师考点范围、题型和难度；
- 排除本卷已有题目和被替换题本身；
- 用当前卷中其他题重新计算单考点占比；
- 使用草稿生成时保存的 `as_of` 和参数，避免几分钟后打开弹窗就改变推荐口径；
- 返回与原推荐相同的分项分数、理由和证据结构。

教师如需改变教学范围，应回到作业设置重新预检，而不是在换题弹窗中静默越界。第一版不必实现任意题库越界强制加入。

### 5.2 快照构造函数

统一函数应保存：

```text
question_snapshot:
  schema_version、original_qid、chapter、section
  question_text、question_type、difficulty、assets
  solution、wrong_analysis（教师反馈后续可用）

options_snapshot:
  当前 options 原样深拷贝

answer_snapshot:
  当前 correct_answer 原样深拷贝

answer_rule_snapshot:
  answer_match_type
  answer_normalization_rule

exam_point_snapshot:
  main{id, name}
  related[{id, name}]
```

JSON 值要深拷贝，不能在内存中与 ORM 当前对象共享可变引用。所有显示和后续 M4 判分都以这些快照为准。

---

## 6. 多学生预检与正式生成

### 6.1 创建请求契约

建议请求：

```json
{
  "idempotency_key": "前端生成的 UUID",
  "title": "有理数阶段练习",
  "student_ids": ["..."],
  "recommendation_parameters": {
    "chapter_code": "math.g6.rational",
    "new_exam_point_codes": [],
    "review_exam_point_codes": [],
    "new_count": 6,
    "review_count": 4,
    "question_types": ["choice", "fill_blank"],
    "base_difficulty": 2,
    "min_difficulty": 1,
    "max_difficulty": 4,
    "avoid_recent_days": 7,
    "max_exam_point_ratio": 0.5
  }
}
```

校验：

- 标题去除首尾空格后非空，限制合理长度；
- 1 至 100 个不重复 UUID；当前演示一般为 5 个；
- 所有学生存在；
- 参数继续由 `RecommendationRequest` 校验；
- 章节暂时仍是 `math.g6.rational`，不要为了 M3 提前假装支持未导入章节；
- 同一 `idempotency_key` 和相同请求返回已有结果；同 key 不同请求返回 409。

### 6.2 多学生预检

建议新增只读接口，在真正写库前一次查看所有学生结果：

```text
POST /api/v1/assignments/preview
```

它应：

1. 取一次 `as_of`；
2. 逐名学生调用现有画像上下文与推荐服务；
3. 返回每名学生的选中数、缺口、题目和理由；
4. 返回班级级汇总，如完整人数、缺口人数和各缺口原因；
5. 前后数据库行数不变。

教师看到缺口后调整题量、范围、难度或占比，再次预检。系统不能自动扩大范围或加入未审核题。

### 6.3 正式生成事务

建议让：

```text
POST /api/v1/assignments
```

直接完成 Assignment、所有学生草稿和题目快照的原子创建，而不是先提交空 Assignment，再分多次生成学生试卷。

伪流程：

```text
validate request and idempotency key
as_of = now(UTC)
load all students
for each student:
    context = load_context(...)
    result = preview(..., as_of=as_of)
    build snapshot payloads in memory

begin one transaction
    insert Assignment
    for each student:
        insert PaperInstance(status=draft, version_no=1, instance_code=null)
        insert ordered PaperInstanceItems
        save recommendation_audit and selection_audit
flush all
commit once
```

可以先读取并计算、后开始写入；但从第一次写入到结束必须是一个事务。小规模 Demo 无需引入异步任务队列。

### 6.4 候选不足

候选不足不是服务器错误。仍可生成部分题目的 draft，并在作业和每名学生卡片上醒目标记：

```text
requested_count
selected_count
new_shortfall
review_shortfall
diagnostics
```

默认发布前阻止未确认的短缺。教师可以：

1. 回到设置修改条件并创建新的有效草稿；或
2. 明确接受较短试卷，在发布请求中提交 `acknowledge_shortfall=true` 和非空原因。

教师主动删题造成的题量下降也使用同一显式确认机制。确认事件写入 `edit_history`。不能只在前端弹一个提示、后端却无条件发布。

---

## 7. 草稿编辑、发布与版本

### 7.1 换题

建议先查询备选，再明确提交选择：

```text
GET  /api/v1/paper-instances/{paper_id}/items/{item_id}/alternatives
POST /api/v1/paper-instances/{paper_id}/items/{item_id}/replace
```

替换请求包含：

```text
replacement_question_id
expected_edit_revision
```

在一个事务中完成：锁定 draft、核对修订号、验证备选仍合规、构造新快照、保持原 `order_no`、写新 `selection_audit`、追加事件、修订号加一。建议保留 `PaperInstanceItem.id` 并更新该行；也可删除重建，但必须确保审计中的 before/after 清楚且不会触发题序唯一冲突。

### 7.2 删除

```text
DELETE /api/v1/paper-instances/{paper_id}/items/{item_id}
```

请求体或查询参数携带 `expected_edit_revision`。删除后将题序压紧为 `1..N`，记录被删题目的 ID、原题号、bucket 和原因。最后一题默认不允许删除；若确实要支持空草稿，发布仍必须阻止。

### 7.3 调序

```text
PUT /api/v1/paper-instances/{paper_id}/order
```

请求发送完整有序 `item_ids` 和 `expected_edit_revision`。后端验证它恰好是当前题目 ID 的一个排列，不能漏项、重复或夹带其他试卷的题。

现有 `(paper_instance_id, order_no)` 是非延迟唯一约束，直接交换 1 和 2 可能在中间状态冲突。使用两阶段更新：

```text
先把所有 order_no 加上一个足够大的正偏移并 flush
再按目标顺序写回 1..N 并 flush
```

不要用负数临时题号，因为数据库已有 `order_no > 0` 检查。

### 7.4 发布

```text
POST /api/v1/paper-instances/{paper_id}/publish
POST /api/v1/assignments/{assignment_id}/publish
```

单份发布和批量发布应调用同一个服务。批量发布先校验全部目标，再一次提交；不能前 3 份成功、第 4 份失败。

请求包含每份试卷的 `expected_edit_revision`，以及必要时的缺口确认。事务中：

1. `SELECT ... FOR UPDATE` 锁定目标草稿；
2. 完成发布完整性校验；
3. 为每份试卷生成不重复短码；
4. 同一次 UPDATE 写入 `status=published`、`instance_code`、`published_at`；
5. flush 触发数据库约束；
6. 全部成功后提交。

发布后不再更新 item，也不再追加 edit history；发布动作本身需要记录时，应在状态切换前写入最终事件。

### 7.5 发布后修订

```text
POST /api/v1/paper-instances/{published_id}/revise
```

只允许从 published 创建新 draft：

- 锁定同一 Assignment 和 Student 的版本集合；
- `version_no = max(version_no) + 1`；
- `supersedes_instance_id = published_id`；
- `instance_code = null`，`published_at = null`；
- 深拷贝旧版全部题目、顺序、答案规则和考点快照；
- `selection_audit.source = version_copy` 并保留原选择信息；
- 新草稿可继续换题、删题和调序。

如同一已发布版本已经有未发布的后继草稿，返回该草稿或 409，避免无意分叉。新版本发布后，旧实例的任何字段都不改变。

### 7.6 按编号定位

```text
GET /api/v1/paper-instances/by-code/{instance_code}
```

只返回精确匹配的已发布实例。可统一转大写和去除用户输入两端空格，但不能：

- 找不到旧码时返回该学生最新版；
- 用学生姓名猜测；
- 接受 draft；
- 在学生不匹配时继续进入提交。

M3 响应必须给出绑定的 `student_id`、`student_code`、Assignment、版本和状态，为 M4 的归属核验提供稳定入口。M4 提交服务还需再次校验，不应只依赖前端。

---

## 8. API 契约建议

### 8.1 作业接口

```text
POST /api/v1/assignments/preview       多学生只读预检
POST /api/v1/assignments               原子创建作业与所有草稿
GET  /api/v1/assignments               分页列出作业
GET  /api/v1/assignments/{id}          作业及各学生试卷摘要
POST /api/v1/assignments/{id}/publish  批量发布选中的草稿
```

作业详情至少返回：学生、版本、状态、实际/目标题量、缺口、最后编辑修订号、实例码和发布时间。

### 8.2 试卷接口

```text
GET    /api/v1/paper-instances/{id}
GET    /api/v1/paper-instances/by-code/{instance_code}
GET    /api/v1/paper-instances/{id}/items/{item_id}/alternatives
POST   /api/v1/paper-instances/{id}/items/{item_id}/replace
DELETE /api/v1/paper-instances/{id}/items/{item_id}
PUT    /api/v1/paper-instances/{id}/order
POST   /api/v1/paper-instances/{id}/publish
POST   /api/v1/paper-instances/{id}/revise
```

### 8.3 响应视图和答案保护

至少区分：

- `PaperInstanceTeacherOut`：含答案、判分规则、推荐理由和编辑历史；供当前教师端使用。
- `PaperInstanceStudentOut`：不含 `answer_snapshot`、`answer_rule_snapshot`、解析和易错分析；供预览/后续打印使用。
- `PaperByCodeOut`：默认只返回归属、版本、状态和题号等最少信息，不直接暴露整份标答。

当前 Demo 没有认证，不能把“响应模型分开”描述为真正的权限控制；上线前仍需身份认证和授权。

### 8.4 HTTP 错误约定

| 情况 | 状态码 |
|---|---:|
| UUID 格式或请求字段非法 | 422 |
| 学生、作业、试卷、题目不存在 | 404 |
| 非 draft 编辑、重复发布、修订号冲突 | 409 |
| idempotency key 被不同请求复用 | 409 |
| 候选题不属于当前卷允许范围 | 422 |
| 未确认缺口就发布 | 409 |

不要把所有业务异常都映射为 500，也不要将数据库触发器报错原文直接返回给页面。

---

## 9. 前端开发

### 9.1 页面和路由

建议增加：

```text
/assignments                       作业列表
/assignments/new                   创建与多学生预检
/assignments/:assignmentId         作业详情、学生试卷状态与批量发布
/paper-instances/:paperInstanceId  单份草稿编辑 / 已发布只读查看
```

对应文件可为：

```text
frontend/src/types/assignment.ts
frontend/src/pages/AssignmentListPage.tsx
frontend/src/pages/AssignmentCreatePage.tsx
frontend/src/pages/AssignmentDetailPage.tsx
frontend/src/pages/PaperInstancePage.tsx
frontend/src/components/AssignmentSettingsForm.tsx
frontend/src/components/PaperItemCard.tsx
frontend/src/components/ReplacementDrawer.tsx
frontend/src/components/RecommendationReason.tsx
```

`RecommendationReason` 和证据抽屉尽量从现有 `RecommendationPreview` 提取复用，不要维护两种理由文案。

### 9.2 创建页

表单按教师思维组织：

1. 作业标题；
2. 选择班级/学生；
3. 新知识范围与题量；
4. 回顾范围与题量；
5. 题型、难度、近期去重和考点上限；
6. 多学生预检；
7. 确认生成草稿。

默认新旧题量可以体现约 60%～70% / 30%～40%，但请求仍使用明确的 `new_count`、`review_count`，避免百分比取整歧义。预检结果显示：

- 每名学生实际题数与缺口；
- 个性化/诊断题数量；
- 主要推荐考点和理由；
- 全班是否可完整生成；
- “预检不会创建作业”的提示。

生成按钮要防重复点击，并使用一次性 idempotency key；请求失败后可重试同一个 key。

### 9.3 作业详情页

每名学生一行或一卡：

```text
学生 / 版本 / 状态
目标题数 / 实际题数 / 缺口
最近编辑修订号
查看或编辑
单份发布
instance_code / published_at
```

支持选择多份 draft 批量发布。已有 published 和 draft 混在同一页时，按钮状态必须清楚，不能让教师误以为全部都已发布。

### 9.4 试卷编辑页

- 顶部显示学生、作业、版本、draft/published 标签、实际题量和缺口。
- draft 明确显示“未发布”，published 明确显示编号和时间。
- 每题显示题号、题干、题型、难度、主考点、bucket、策略和推荐理由。
- 推荐证据继续可打开现有作答抽屉。
- 换题打开备选抽屉；确认后整个卡片以服务端响应替换。
- 删除必须二次确认。
- 调序首版用“上移/下移”或受控排序即可，无需为了拖拽再引入大型依赖。
- published 页面不渲染编辑控件；即使前端误发请求，后端仍返回 409。
- 学生预览绝不能出现标准答案和判分规则。

收到 409 修订号冲突时，不覆盖本地界面，提示“草稿已在别处修改，请刷新”，并重新拉取服务端版本。

### 9.5 导航与移动端

在 `AppShell` 增加“组卷/作业”入口，并将副标题从 M2 更新为 M3。390px 宽度下：

- 表格允许局部横向滚动，但页面不能全局横向溢出；
- 题目资源图片不超出卡片；
- 长考点名和实例码可复制；
- 发布确认弹窗按钮可见；
- 换题后不能短暂显示上一名学生的数据。

---

## 10. 建议实施顺序

每个批次完成后验证退出条件，再继续。

| 步骤 | 工作内容 | 退出条件 |
|---|---|---|
| M3-1 | 固定请求/响应、审计 JSON 和发布不变量 | schema 单测覆盖非法学生、重复 ID、题量和参数 |
| M3-2 | 新增 0005、ORM 字段和迁移回归 | 迁移成功，旧 150 条历史与 15 份试卷不变，M1/M2 验收通过 |
| M3-3 | 快照构造与多学生只读预检 | 5 人共用 as_of，预检前后数据库计数不变 |
| M3-4 | 原子生成 Assignment 和 draft | 一次生成 1 个作业和 5 份草稿；故障时全部回滚 |
| M3-5 | 替换候选、换题、删题、调序 | 只改 draft，顺序始终连续，审计和修订号正确 |
| M3-6 | 单份/批量发布、编号查询、复制新版本 | 发布后数据库层不可改；旧新编号各自定位 |
| M3-7 | 接入作业列表、创建页、编辑页和发布交互 | 教师可从设置到发布连续操作，无需 Swagger |
| M3-8 | 自动验收、E2E、README 和交接 | 全部回归通过，实际统计和限制记录完整 |

第一条纵向链路建议只跑通：

```text
S001 + S002
→ 多学生预检
→ 原子生成两份 draft
→ S001 换一题并调序
→ 发布 S001
→ 按实例码找回完全相同的快照
```

链路稳定后再扩展到 5 人、批量发布和新版本。

---

## 11. 测试与自动验证

### 11.1 必需测试矩阵

| 测试 | 关键断言 |
|---|---|
| 多学生预检只读 | Assignment、PaperInstance、Item 数量不变 |
| 共同时间基准 | 同批所有推荐审计的 `as_of` 完全一致且带时区 |
| 原子生成 | 5 人全部成功；中途构造失败时 0 个 M3 对象落库 |
| 请求幂等 | 同 key 同请求返回同一 Assignment；不同请求冲突 |
| 推荐复用 | 生成结果和固定 as_of 的 M2 服务输出一致，不存在路由专用打分 |
| 审计完整 | 参数、规则版本、权重、分项、理由、证据修订号和缺口均落库 |
| 快照完整 | 题干、选项、资源、答案、规则、主/关联考点可独立读取 |
| 快照隔离 | 修改当前 Question 后，draft 和 published 展示都不变 |
| 换题 | 排除本卷重复题，保持题号和 bucket，更新选择审计与修订号 |
| 删除 | 只允许 draft；题序压紧；删除事件保留 |
| 调序 | 只接受当前 item 的完整排列；交换不触发唯一约束 |
| 并发编辑 | 旧 `expected_edit_revision` 返回 409，不覆盖新内容 |
| 缺口 | 不放宽范围；未确认不能发布；确认原因进入审计 |
| 发布 | 编号唯一，状态/编号/时间一次写入，重复发布冲突或幂等返回 |
| 数据库防篡改 | published 实例 UPDATE/DELETE 和 item INSERT/UPDATE/DELETE 均失败 |
| 修订版 | 深拷贝旧快照，版本加一、指向旧版、编号为空，旧版不变 |
| 编号定位 | 旧码找旧版、新码找新版、未知码 404、draft 不可按码找到 |
| 答案隔离 | StudentOut / 按码摘要不泄露标答和判分规则 |
| M2 回归 | 推荐预览仍只读，画像和推荐纯函数测试全部通过 |

纯推荐和编号生成可做单元测试；事务、触发器、行锁、JSONB 和唯一约束必须在 PostgreSQL 集成测试中验证，不能只用 SQLite 替代。

### 11.2 测试数据隔离

现有 M1/M2 测试依赖 5 名学生、30 组题和 150 条历史，不要把临时第 6 名学生或测试 Assignment 永久写入演示库。

集成测试使用事务回滚或独立测试数据库。测试发布后无需执行 DELETE 清理；最外层事务回滚即可撤销，避免触发已发布对象删除保护。

### 11.3 verify_m3.py

新增：

```text
backend/scripts/verify_m3.py
```

默认只读、失败返回非零退出码。对已经通过页面创建的 M3 演示作业建议输出：

```text
m3_assignments
m3_paper_instances
draft_instances
published_instances
unique_instance_codes
student_assignment_version_duplicates
duplicate_questions_within_paper
non_contiguous_orders
incomplete_snapshots
missing_recommendation_audits
missing_selection_audits
invalid_supersedes_links
published_without_code_or_time
validation_errors
```

不要写死每名学生一定选到不同题，也不要写死具体 UUID、随机实例码或随时间变化的推荐分。固定算法断言放在使用固定 `as_of` 的测试夹具中。

### 11.4 迁移版本测试

新增 0005 后，以下现有检查会失败：

```text
backend/tests/test_api.py
backend/scripts/verify_m1.py
```

它们目前把迁移版本写死为 `0004`。应改为核对新的实际 Alembic head（此阶段为 `0005`），不能删除迁移检查，也不能修改 0001～0004 冒充没有新迁移。

### 11.5 完成实现后的命令

数据库启动后执行：

```powershell
Set-Location 'D:\desktop\math-agent\code\backend'
$env:DATABASE_URL = 'postgresql+psycopg://postgres@localhost:55432/math_agent'

& '..\.conda-env\python.exe' -m alembic upgrade head
& '..\.conda-env\python.exe' -m pytest -q
& '..\.conda-env\python.exe' -m scripts.verify_m1
& '..\.conda-env\python.exe' -m scripts.verify_m2
& '..\.conda-env\python.exe' -m scripts.verify_m3
```

前端和 M0 回归：

```powershell
Set-Location 'D:\desktop\math-agent\code\frontend'
npm run build
npm run test:e2e

Set-Location 'D:\desktop\math-agent\code'
& '.\.conda-env\python.exe' '.\scripts\validate_m0.py'
```

再次执行种子导入时，M3 用户创建的作业不能被删除或改写；种子导入仍应幂等。

---

## 12. 手工演示与验收路径

1. 打开组卷页，选择 5 名虚拟学生和明确的新知识/回顾范围。
2. 点击预检，确认没有新建数据库作业，并查看不同学生的推荐理由或相同题目的合理原因。
3. 故意设置过窄条件，确认页面显示每个 bucket 的缺口，系统未跨范围补题。
4. 调整条件并正式生成，确认一次 Assignment 下出现 5 份 draft，全部标记“未发布”。
5. 打开 S001，查看每题理由和证据；换一题、删除一题、上下移动题目。
6. 在另一个标签保留旧页面，先在第一个标签编辑，再用旧修订号提交，确认返回冲突且新内容不被覆盖。
7. 发布 S001，记录实例码；确认页面变为只读，直接调用编辑接口也被拒绝。
8. 修改一条当前 Question 的题干或答案（仅在隔离测试中），确认 S001 发布快照不变。
9. 从 S001 已发布试卷创建 v2，替换题目后发布，确认得到新码。
10. 分别输入 v1、v2 编号，确认定位两个不同版本，v1 内容没有改变。
11. 批量发布其余学生；若存在短缺，确认必须填写接受原因。
12. 在 390px 窄屏检查创建表单、题目卡、替换抽屉和发布弹窗，无全局横向溢出。

M3 验收不要求调用任何大模型，也不要求生成 Word/PDF。核心是工作流、推荐解释、快照和版本正确。

---

## 13. M3 完成后应提交的产物

```text
backend/alembic/versions/0005_add_paper_generation_audit.py
backend/app/models/entities.py 的 M3 增量字段
backend/app/schemas/assignments.py
backend/app/schemas/papers.py
backend/app/services/paper_snapshots.py
backend/app/services/paper_generation.py
backend/app/services/paper_editing.py
backend/app/services/paper_publication.py
backend/app/api/routes/assignments.py
backend/app/api/routes/papers.py
backend/scripts/verify_m3.py
M3 后端单测与 PostgreSQL 集成测试
作业创建、详情和试卷编辑前端页面
M3 Playwright 场景
code/README.md 的 M3 启动、接口与验收说明
code/docs/07_M3_工作总结与M4交接.md
```

工作总结至少记录：

- 实际新增迁移和字段；
- 实际作业、draft、published、新旧版本数量；
- 实例码格式；
- 缺口处理策略；
- 通过的后端、前端和 E2E 数量；
- 一个换题审计示例；
- 一个旧版/新版按码定位示例；
- 尚未完成且交给 M4/M6 的事项。

### 13.1 给 M4 的稳定输入

M4 应只接收明确的 published `paper_instance_id`，并从它的 `PaperInstanceItem` 读取：

```text
最终题号
question_id
answer_snapshot
answer_rule_snapshot
exam_point_snapshot.main
```

M4 不得回到当前 Question 找标答，也不得把旧实例码解析到新版。手工提交时还要验证 Submission.student_id 与 PaperInstance.student_id 一致。完成判分后，再复用 M2 的重放服务更新画像。

### 13.2 给 M6 的稳定输入

M6 从 published 快照生成学生版正文和统一答题区，两处打印同一个 `instance_code`。学生版不含答案；教师答案页另行生成。M3 不提前实现 PDF、二维码或 VLM。

---

## 14. 常见返工点

1. **把 M2 推荐代码复制进组卷路由。** 后续规则修改会出现预览和正式试卷不一致。
2. **只保存 question_id。** 题库改动后旧试卷的题干、答案和判分规则会漂移。
3. **在发布时重新读取 Question。** 教师确认的草稿可能在发布瞬间悄悄变化。
4. **只靠前端禁用按钮保护 published。** 必须同时有服务校验和现有数据库触发器。
5. **给 draft 提前分配实例码。** 被放弃的草稿会消耗或混淆纸面编号。
6. **直接交换 order_no。** 非延迟唯一约束会在中间更新状态报冲突。
7. **删除题后留下 1、2、4 的题号。** 后续答题区和手工录入都会歧义。
8. **推荐不足时自动放宽范围。** 这违背教师教学边界和现有需求。
9. **教师删题后无确认直接发布。** 目标题量与实际题量不一致却无审计。
10. **发布后原地改成 v2。** 旧作答无法知道当时使用哪个答案。
11. **旧编号自动跳最新版。** M4 会拿新版标答批改旧试卷。
12. **答案和判分规则进入学生预览。** 即使是 Demo，也应从响应模型上分开。
13. **在学生循环中 commit。** 中途失败会留下不完整班级作业。
14. **测试清理时 DELETE published。** 会被 0004 触发器正确拒绝；应使用外层事务回滚。
15. **为了新增迁移而改写 0004。** 已应用迁移不能重写，必须新增 0005。

---

## 15. M3 最终验收清单

- [ ] 多学生预检只读，所有学生共用同一带时区 `as_of`。
- [ ] 一次事务创建 1 个 Assignment 和选中学生各自的 draft。
- [ ] 正式生成完全复用 M2 推荐服务，固定时间下与预览结果一致。
- [ ] 推荐参数、规则版本、分项、理由、证据和候选缺口均已保存。
- [ ] 每题已保存题干、选项、资源、答案、判分规则和考点快照。
- [ ] 教师可以换题、删题、调序，题序始终是连续的 `1..N`。
- [ ] 草稿并发编辑使用独立 `edit_revision`，旧请求不会覆盖新内容。
- [ ] 候选不足不静默放宽；发布较短试卷必须明确确认并记录原因。
- [ ] 发布时生成唯一实例码，published 之后服务层和数据库层均不可修改。
- [ ] 发布后改题通过新版本完成，`supersedes_instance_id`、版本号和新编号正确。
- [ ] 旧编号定位旧版，新编号定位新版，未知或不匹配时不猜测。
- [ ] 学生视图和按码摘要不泄露标准答案及判分规则。
- [ ] 至少 5 名虚拟学生可在同一 Assignment 下生成独立试卷，差异有依据但不强求两两不同。
- [ ] M0、M1、M2 回归，M3 后端测试、前端构建与 Playwright 全部通过。
- [ ] README 和 `07_M3_工作总结与M4交接.md` 已按实际实现更新。

---

## 16. 现在最先做的一件事

先定义 `assignments.py` / `papers.py` 的请求响应和三份审计 JSON 样例，再写 0005 迁移与模型字段。不要先画完整页面，也不要先写发布按钮。

第一天的可验证目标是：

```text
固定 as_of 调用现有 M2 推荐服务
→ 为 S001 构造一份内存中的完整试卷快照
→ 将参数、推荐输出和每题选择原因按既定 schema 序列化
→ 运行 schema 单测与 M2 推荐回归
```

这条链路稳定后，再把它放进“1 个 Assignment + 5 个 draft”的单事务持久化流程。这样 M3 的核心首先是可解释且可冻结的数据合同，而不是一组暂时能点的页面按钮。
