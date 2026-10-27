# 数学教学 Agent Demo 第六步开发指导：M5 可视化、演示与质量保障

> 适用阶段：M0、M1、M2、M3、M4 已完成并通过各阶段验收之后  
> 本阶段目标：把已经跑通的内部业务闭环整理成稳定、可解释、可重复演示的教师工作台  
> 当前基线：Alembic `0012`；后端 79 项测试通过；`verify_m1.py`～`verify_m4.py` 均为 0 个错误；M4 Playwright 桌面端与 390px 移动端共 8 项通过  
> 阶段边界：M5 不接 OCR/VLM，不做打印答题区，不改变 M4 判分和画像语义；这些内容属于 M6

---

## 1. M5 要交付什么

M5 不是再增加一条新的核心业务链路，而是把 M0～M4 已有能力组织成教师能够看懂、能够操作、出现异常时能够解释的演示版。

最终应形成以下闭环：

```text
固定演示数据
→ 班级学情总览
→ 定位需关注的考点和学生
→ 查看具体作答证据
→ 查看作业完成 / 待复核状态
→ 完成人工复核或历史改判
→ 班级统计与个人画像同步变化
→ 再次组卷并展示推荐依据
```

### 1.1 本阶段必须完成

1. 新增班级学情总览页；
2. 展示班级考点正确率、掌握度参考值、证据覆盖和未评估状态；
3. 展示作业完成、未提交、待复核和已完成数量；
4. 班级统计可以下钻到学生画像、作答证据、批改页和作业详情；
5. 固定一套可从空数据库构造的 M5 演示数据和演示路径；
6. 新增 M5 后端测试、前端 E2E、全链路业务回归和 `verify_m5.py`；
7. 完成路由懒加载、加载态、空态、错误态、移动端和基本无障碍检查；
8. 更新 README、演示脚本和 M5 工作总结，为 M6 提供稳定输入。

### 1.2 明确不在 M5 实现

- 图片上传、OCR/VLM、识别置信度和原图核对；
- 打印版试卷、统一答题区、二维码；
- 登录、教师账号、角色权限和多租户；
- 正式的 `Class`、班级成员历史和学期管理模型；
- 分值、步骤分、小题部分掌握度；
- 新的能力测评模型、IRT/BKT 或经过校准的能力分；
- 没有教师核对映射时强行绘制能力雷达图；
- 用 LLM 生成班级结论或替代确定性统计。

### 1.3 M5 完成的判断标准

M5 完成后，第一次接触项目的老师应能在 8～10 分钟内回答：

```text
这个班哪些考点值得优先关注？
结论用了多少学生、多少次确认作答？
哪些学生还没有证据，而不是“能力低”？
当前作业有多少未提交、待复核和已完成？
某个统计值可以追溯到哪些学生和作答吗？
改判后为什么班级统计和个人画像发生了变化？
下一份个性化练习为什么给不同学生选了不同题？
```

---

## 2. 当前基础与开工检查

### 2.1 已有成果

M5 应直接复用以下稳定能力，不重新实现第二套逻辑：

- M1：题库、考点目录、5 名虚拟学生和固定历史作答；
- M2：可重放画像、考点条形图、掌握度曲线、近期错误、证据抽屉和推荐解释；
- M3：个性化组卷、换题、排序、发布快照、版本和实例码；
- M4：手动录入、确定性判分、待复核、教师修订、追加式审计和事务画像重算；
- `/grading`：试卷级未录入、待复核、已完成状态；
- `Student.class_name`：当前 Demo 的班级归属字段；
- 当前 `六年级 Demo 班` 的 S001～S005：M5 的默认演示班。

### 2.2 优先阅读的现有文件

```text
03_数学教学Agent_Demo开发步骤规划.md
02_数学教学Agent_Demo技术设计方案.md
08_数学教学Agent_Demo第五步开发指导_M4.md

code/docs/11_M4_工作总结与M5交接.md
code/README.md

code/backend/app/services/student_profile.py
code/backend/app/services/mastery.py
code/backend/app/services/submission_workflow.py
code/backend/app/services/paper_workflow.py
code/backend/app/models/entities.py

code/frontend/src/pages/StudentDetailPage.tsx
code/frontend/src/pages/GradingQueuePage.tsx
code/frontend/src/components/MasteryBarChart.tsx
code/frontend/src/components/MasteryHistoryChart.tsx
code/frontend/src/components/AttemptEvidenceDrawer.tsx
code/frontend/src/App.tsx
```

### 2.3 开工基线

在修改代码前记录以下结果。已有数据库中包含个人调试数据时，不要把总行数写成验收常量。

```powershell
cd code
docker compose up -d postgres

cd backend
alembic upgrade head
python -m pytest
python scripts/verify_m1.py
python scripts/verify_m2.py
python scripts/verify_m3.py
python scripts/verify_m4.py

cd ..\frontend
npm run typecheck
npm run build
npm run test:e2e
```

开工时还要保存一次前端构建产物大小，作为路由拆分前基线。M5 的目标是减少首页加载内容和消除已有的主包体积提示，不能通过调大 `chunkSizeWarningLimit` 隐藏问题。

---

## 3. 先固定 M5 的十二条业务不变量

### 3.1 聚合结果必须来自当前有效证据

班级统计必须复用 M2 的证据校验和重放语义。只有当前修订为以下状态的有效 Attempt 才进入正确率和 mastery 聚合：

```text
auto_confirmed
teacher_confirmed
```

`needs_review` 不进入分子，也不进入分母。

### 3.2 待复核不等于答错

页面必须把以下概念分开：

```text
答错：is_correct = false，且状态已确认
待复核：is_correct = null
未提交：没有 Submission
未评估：没有该考点的有效确认作答
异常证据：因归属、快照或修订问题被排除
```

不能用红色错误数包含待复核，不能把待复核计入正确率分母。

### 3.3 正确率与掌握度不是同一个指标

```text
正确率 = 确认答对次数 / 确认作答次数
mastery = 按 answered_at 和 id 重放 EMA 后的当前参考值
```

正确率用于说明已有作答结果；mastery 用于组卷参考。页面标题、Tooltip 和接口字段不能把二者混称为“能力”。

### 3.4 班级均值只能平均有证据的学生

某考点的 `average_mastery` 只平均 `mastery != null` 的学生。未评估学生不按 0，也不按内部冷启动值 0.5 参与平均。

同时返回：

```text
student_count
evidence_student_count
stable_student_count
unassessed_student_count
```

只展示均值而不展示覆盖人数会产生误导，因此不允许。

### 3.5 “有证据”与“证据较稳定”必须分开

- `evidence_student_count`：至少有 1 次有效确认作答；
- `stable_student_count`：该学生该考点达到 M2 的 `assessment_status = assessed`；
- `insufficient` 仍可展示 mastery，但使用灰色或“证据较少”标记；
- `unassessed` 显示“未评估”，不画 0 分柱。

### 3.6 班级范围沿用现有 `class_name`

M5 以未归档学生的 `Student.class_name` 作为班级筛选条件，不新建 `Class` 表。原因是当前只有一个演示班，没有成员转班、学期或任课关系需求。

若后续需要真实班级、历史成员关系或多教师权限，再独立设计 `Class`、`ClassMembership` 和学期模型，不能在 M5 临时拼出半套模型。

### 3.7 作业进度只统计当前有效目标版本

对同一 Assignment 和学生：

- 当前有效、已发布的 PaperInstance 是当前作业目标；
- 已被 `superseded` 或 `withdrawn` 的旧版本不重复计入当前进度；
- 旧版本已有提交时可以显示在历史信息中，但不增加当前目标人数；
- 草稿与已发布目标分开展示；
- 当前目标分母为有效已发布试卷数，不以全班人数强行补齐。

### 3.8 所有统计使用发布快照的主考点

历史作答的考点归属来自 `PaperInstanceItem.exam_point_snapshot.main.id`，不回读当前 Question 的考点覆盖历史。题库后续修改不能悄悄改变旧统计。

### 3.9 当前修订是唯一当前结论

AttemptRevision 用于审计，不是把所有历史修订都计为多次作答。班级统计读取 Attempt 当前状态，并验证其 `revision_no` 对应修订存在。

### 3.10 聚合逻辑放在后端

正确率分母、证据排除、版本选择和状态归类必须由后端统一计算。前端只负责格式化与交互，不能下载多组明细后自行拼接业务统计。

### 3.11 能力雷达图必须有教师核对前置条件

当前项目没有经教师核对的“考点—能力维度”映射。因此 M5 默认使用考点条形图、正确率图和变化曲线，不绘制虚假的能力雷达图。

只有同时满足以下条件才可开启雷达图：

1. 映射配置有版本号；
2. 每条映射有教师确认人和确认时间；
3. 每个能力维度有明确聚合规则；
4. 页面展示支持考点数、不同题目数和确认作答数；
5. 无证据维度显示“暂未评估”，不补 0 或 50%。

### 3.12 M5 不能破坏 M4 的审计约束

固定演示数据不能靠删除 AttemptRevision 来“恢复现场”。`AttemptRevision` 仍保持不可更新、不可删除。重复演示使用隔离的演示数据库重新初始化，不能为方便重置而放松数据库守卫。

---

## 4. 班级学情指标口径

### 4.1 班级学生范围

```text
Student.class_name = 请求的 class_name
AND Student.archived_at IS NULL
```

默认演示值：

```text
class_name = 六年级 Demo 班
chapter_code = math.g6.rational
```

班级列表接口只返回至少有一名未归档学生的班级，并按班级名稳定排序。

### 4.2 考点行

每个考点返回：

```text
exam_point_id
exam_point_code
exam_point_name
student_count
evidence_student_count
stable_student_count
unassessed_student_count
confirmed_attempt_count
correct_attempt_count
incorrect_attempt_count
accuracy
average_mastery
recent_wrong_count
available_question_count
```

计算公式：

```text
accuracy = correct_attempt_count / confirmed_attempt_count
average_mastery = sum(有证据学生 mastery) / evidence_student_count
unassessed_student_count = student_count - evidence_student_count
```

当分母为 0 时，`accuracy` 和 `average_mastery` 返回 `null`，不能返回 0。

`recent_wrong_count` 复用每名学生每考点最近 5 次有效证据窗口后求和。页面必须标注“各学生最近窗口合计”，不能伪装成统一时间窗口。

### 4.3 班级总览卡片

建议展示：

```text
在册学生数
有任一有效证据的学生数
确认作答数
待复核题数
异常证据数
已覆盖考点数
未覆盖考点数
```

不展示“班级总能力 78 分”一类没有测量依据的合成分。

### 4.4 需关注考点

只从有证据的考点生成确定性排序：

```text
第一排序：average_mastery 升序
第二排序：accuracy 升序
第三排序：confirmed_attempt_count 降序
第四排序：考点目录顺序
```

建议分组：

```text
重点练习：stable_student_count > 0 且 average_mastery < 0.4
近期关注：recent_wrong_count > 0，但未达到重点练习条件
补充证据：有证据，但 stable_student_count 较少
安排诊断：全班该考点均未评估且题库有可用题
```

这些标签是教学工作台提示，不是学生诊断结论。每一项都必须附人数和作答数。

### 4.5 作业进度

对当前有效已发布目标：

```text
unsubmitted：无 Submission
needs_review：已有 Submission，至少一个当前 Attempt 为 needs_review
completed：已有 Submission，且没有 needs_review
```

另外单独返回：

```text
draft_count
active_published_count
historical_invalidated_count
submitted_count
unsubmitted_count
needs_review_count
completed_count
submission_rate
completion_rate
```

```text
submission_rate = submitted_count / active_published_count
completion_rate = completed_count / active_published_count
```

分母为 0 时返回 `null`。待复核已经提交，但尚未完成，因此进入 submission rate，不进入 completion rate。

### 4.6 学生下钻行

某个考点的学生明细至少包含：

```text
student_id
student_code
student_name
assessment_status
mastery
attempt_count
correct_count
accuracy
recent_wrong_count
last_practiced_at
```

默认排序：已评估且需要关注的学生在前，未评估学生在后；同组内按 `student_code` 稳定排序。点击学生进入现有 `/students/:studentId`，不要另造一套画像详情。

---

## 5. 后端 API 契约

### 5.1 推荐文件

```text
backend/app/schemas/analytics.py
backend/app/services/class_analytics.py
backend/app/api/routes/analytics.py
backend/tests/test_class_analytics.py
backend/tests/test_m5_workflow.py
backend/scripts/verify_m5.py
```

在 `app/api/router.py` 注册新路由。M5 预计不需要数据库迁移；若实现过程中发现必须增加持久化字段，应先说明为什么不能实时计算，再单独新增 `0013`，不得改写既有迁移。

### 5.2 路由

```text
GET /api/v1/analytics/classes
GET /api/v1/analytics/class-overview
GET /api/v1/analytics/classes/exam-points/{exam_point_code}/students
GET /api/v1/analytics/assignments/{assignment_id}
```

建议参数：

```text
/analytics/class-overview
  ?class_name=六年级%20Demo%20班
  &chapter_code=math.g6.rational

/analytics/classes/exam-points/{code}/students
  ?class_name=六年级%20Demo%20班
  &chapter_code=math.g6.rational
```

班级名放 query 参数，避免中文、空格和斜杠造成路径歧义。

### 5.3 响应骨架

```json
{
  "class_name": "六年级 Demo 班",
  "chapter_code": "math.g6.rational",
  "computed_at": "2026-09-29T10:00:00Z",
  "rule_version": "m2-ema-v1",
  "summary": {
    "student_count": 5,
    "evidence_student_count": 5,
    "confirmed_attempt_count": 150,
    "pending_review_count": 0,
    "invalid_evidence_count": 0,
    "covered_exam_point_count": 10,
    "uncovered_exam_point_count": 3
  },
  "exam_points": [
    {
      "exam_point_code": "exam.math.g6.rational.example",
      "exam_point_name": "示例考点",
      "student_count": 5,
      "evidence_student_count": 3,
      "stable_student_count": 2,
      "unassessed_student_count": 2,
      "confirmed_attempt_count": 9,
      "correct_attempt_count": 5,
      "incorrect_attempt_count": 4,
      "accuracy": 0.5556,
      "average_mastery": 0.4387,
      "recent_wrong_count": 3,
      "available_question_count": 6
    }
  ],
  "focus_points": [],
  "evidence_note": "正确率只统计有效确认作答；未评估学生不参与掌握度均值。"
}
```

示例中的数值只展示结构，不应写成测试中的固定业务结果。测试通过构造夹具计算预期值。

### 5.4 错误约定

```text
400：参数格式错误
404：班级、考点或 Assignment 不存在
422：chapter_code 与考点不匹配，或聚合请求不满足合同
500：未预期内部错误
```

错误响应沿用现有统一结构。前端不能靠解析中文异常字符串判断错误类型。

---

## 6. 班级聚合服务设计

### 6.1 推荐函数

```python
def list_classes(db: Session) -> list[ClassOption]: ...

def build_class_overview(
    db: Session,
    *,
    class_name: str,
    chapter_code: str,
) -> ClassOverviewOut: ...

def build_exam_point_students(
    db: Session,
    *,
    class_name: str,
    chapter_code: str,
    exam_point_code: str,
) -> ExamPointStudentListOut: ...

def build_assignment_overview(
    db: Session,
    assignment_id: UUID,
) -> AssignmentAnalyticsOut: ...
```

### 6.2 复用画像语义

第一版只有 5 名演示学生，可以为班级内每名学生调用 `load_context()` 和 `mastery_items()`，优先保证与个人画像完全一致。不要复制一份简化 SQL 后遗漏：

- 当前 revision；
- `needs_review` 排除；
- 发布状态；
- 快照编码映射；
- 归属一致性；
- 异常证据排除；
- 以 `(answered_at, id)` 重放。

如果实测出现明显 N+1 性能问题，再把 `load_context` 拆成批量加载器，并让个人画像与班级画像共同调用同一底层函数。不能为了减少查询而产生两套证据定义。

### 6.3 不持久化聚合结果

M5 数据量很小，班级统计实时计算即可。暂不增加聚合表或缓存，避免教师改判后缓存未失效。

若后续班级规模扩大，再基于明确的失效事件设计缓存：

```text
首次提交成功
教师确认 / 修正 / 撤销
重新评分
学生归档或恢复
```

### 6.4 数值与排序

- 内部计算使用 Decimal 或已有量化规则；
- API 输出统一保留 4 位小数；
- 分母为 0 返回 `null`；
- 不使用数据库默认行序；
- 考点按知识点、考点 `sort_order` 和 code 稳定排序；
- 学生按 `student_code` 排序；
- 作业按 `created_at DESC, id` 排序。

### 6.5 作业版本选择

每名学生在一个 Assignment 下可能存在多个 PaperInstance。聚合时显式分组：

```text
active published：status=published AND invalidated_at IS NULL
draft：status=draft
historical invalidated：invalidated_at IS NOT NULL
```

如果同一学生出现两个 active published 版本，这是数据异常，接口应返回明确错误并使 `verify_m5.py` 失败，不能任选一个。

---

## 7. 班级学情前端

### 7.1 页面与导航

新增：

```text
/analytics
src/pages/ClassAnalyticsPage.tsx
src/types/analytics.ts
src/components/ClassExamPointChart.tsx
src/components/AssignmentProgressPanel.tsx
src/components/ExamPointStudentDrawer.tsx
```

主导航增加“学情”入口，版本标识更新为 `有理数 Demo · M5`。默认首页建议从 `/questions` 调整为 `/analytics`，使教师进入系统后先看到需要处理的事情。

### 7.2 页面信息层级

建议按以下顺序：

```text
班级 / 章节筛选
→ 总览卡片
→ 待处理事项（待复核、未提交）
→ 考点学情图表
→ 需关注考点表
→ 作业进度
→ 数据口径说明
```

首屏不应塞入所有审计明细。教师先看到结论，再通过抽屉和链接下钻证据。

### 7.3 考点图表

推荐使用横向条形图：

- 一个视图展示正确率；
- 一个视图展示有证据学生的 mastery 均值；
- 标签旁同时显示“3/5 名学生有证据、9 次确认作答”；
- 无证据考点显示“未评估”，不画 0 柱；
- 证据较少使用灰色，不使用与高风险相同的红色；
- 点击考点打开学生下钻抽屉；
- 图表下方保留等价数据表，不能让颜色成为唯一信息来源。

正确率可以显示为百分比；mastery 仍标注“0～1 参考值”，不要改名为掌握百分比。

### 7.4 待处理事项

页面顶部应有直接行动入口：

```text
待复核 N 题 → /grading?status=needs_review
未提交 N 份 → /grading?status=unsubmitted
查看最近作业 → /assignments/:assignmentId
```

如果现有 `GradingQueuePage` 只从本地 state 读取筛选，M5 应支持 URL query，使演示链接可以刷新和分享。

### 7.5 下钻交互

点击考点后打开学生列表抽屉：

- 有证据学生显示 mastery、正确率、作答数和最近练习时间；
- 未评估学生明确显示“未评估”；
- 点击学生跳转已有学生画像；
- 在个人画像中继续点击曲线点，打开已有作答证据抽屉；
- 不在班级页重复实现完整证据详情。

### 7.6 空态和异常态

至少覆盖：

```text
没有班级
班级没有学生
班级有学生但没有确认作答
章节没有考点
没有 Assignment
Assignment 只有草稿
接口加载失败
部分异常证据被排除
```

空态要给出下一步，例如“前往组卷”“前往批改”“切换章节”，不要只显示空白卡片。

---

## 8. 个人画像与推荐解释的 M5 打磨

M2 已有考点条形图、变化曲线、近期错题和推荐原因。M5 不重写页面，重点统一说明和跳转。

### 8.1 必须保留的说明

```text
掌握度是选题参考值，不是经过校准的能力测评分数。
证据量分档不是统计置信概率。
低证据量不代表能力弱。
未评估不是 0 分。
OCR 置信度属于 M6，不能与画像证据量混用。
```

### 8.2 推荐依据链路

教师应能从推荐题看到：

```text
总推荐分
→ weakness / recent_wrong / difficulty_match / forgetting 分项
→ 对应考点当前画像
→ 支持这一画像的 Attempt
→ 具体试卷快照和当前修订
```

如果当前页面已有这些内容，只需补充跨页面链接和口径，不新增重复面板。

### 8.3 能力雷达图处理

M5 默认不实现雷达图。若老师在开发期间提供并签字确认映射，可作为独立可选批次实现：

```json
{
  "schema_version": "1.0",
  "reviewed_by": "教师姓名或演示代号",
  "reviewed_at": "2026-09-29T00:00:00Z",
  "dimensions": [
    {
      "code": "calculation",
      "name": "计算能力",
      "exam_point_codes": ["..."],
      "aggregation": "weighted_mean_by_confirmed_attempt"
    }
  ]
}
```

映射未通过校验时不渲染雷达图，并回退到考点图表。不能在代码中用考点名称关键词临时猜能力维度。

---

## 9. 固定演示数据

### 9.1 为什么不能只复用日常开发库

日常开发库会经历换题、提交、改判和题库审核，状态会逐渐漂移。M5 必须能从空数据库生成已知场景，保证演示顺序和结果可重复。

同时，M4 已规定 AttemptRevision 只能追加，不能通过删除修订来倒带。因此演示重置必须使用隔离数据库重新初始化。

### 9.2 推荐文件

```text
data/seed/m5_demo_scenarios.json
backend/app/services/demo_scenarios.py
backend/scripts/prepare_m5_demo.py
scripts/reset_m5_demo.ps1
```

场景配置至少包含：

```text
scenario_id
schema_version
fixed_as_of
class_name
student_codes
assignment_seed_key
assignment_title
question original_qid 列表
每名学生的演示答案与目标状态
预期聚合摘要
```

不要在场景文件中保存数据库 UUID；使用 `student_code`、`original_qid`、`seed_key` 等稳定业务键，在构造时解析 UUID。

### 9.3 两套场景

建议提供：

#### `m5_start`

- 已有 M1 固定历史；
- 有一份面向 5 名学生的有效已发布作业；
- 尚未提交；
- 用于从录入开始演示完整闭环。

#### `m5_showcase`

- S001：已完成，保留明显薄弱考点；
- S002：存在 1 个 `needs_review`；
- S003：未提交；
- S004：已完成且整体稳定；
- S005：包含一次“错改对”或“确认后撤销”的修订历史；
- 班级页同时出现未提交、待复核、已完成、低证据量和未评估状态。

场景数据必须调用现有组卷、发布、提交和复核服务生成。除 M1 固定历史外，不直接手写 Attempt 绕过业务守卫。

### 9.4 `prepare_m5_demo.py`

推荐参数：

```text
--scenario m5_start|m5_showcase
--fixed-as-of 2026-09-29T10:00:00+08:00
--check-only
```

要求：

- 只负责在满足前置条件的空白演示环境中构造或核验场景；
- 同一场景重复执行时返回 unchanged，不能重复创建证据；
- 场景状态与配置冲突时明确失败，不擅自删除审计数据；
- 全部步骤使用一个清晰的事务边界；
- 输出机器可读 JSON 摘要。

### 9.5 安全重置

`reset_m5_demo.ps1` 只能重建隔离的 M5 演示数据库或独立 Docker Compose project，至少设置以下守卫：

```text
APP_ENV 必须为 demo
数据库名必须精确等于 math_agent_m5_demo
Compose project 名必须精确等于 math-agent-m5-demo
必须显式传入 -ConfirmResetM5Demo
执行前再次输出实际数据库名和目标 volume
```

禁止：

- 对默认开发库执行 `TRUNCATE`；
- 临时关闭 `AttemptRevision` 保护触发器；
- 使用模糊 volume 名或通配符删除；
- 在脚本中删除用户日常开发数据；
- 把“删除所有数据再导入”做成网页按钮。

### 9.6 时间稳定性

固定场景使用显式 `fixed_as_of` 和固定 `answered_at`，不能在种子脚本中到处调用 `datetime.now()`。否则“最近错误”“遗忘间隔”和推荐顺序会随演示日期变化。

线上请求的 `computed_at` 仍返回真实计算时间；影响规则结果的基准时间由服务参数显式传入，并在审计中保存。

---

## 10. 关键业务回归场景

M5 必须把规划中的回归要求固化为自动测试。

### 10.1 发布前替题

```text
生成草稿
→ 替换第 3 题
→ 发布
→ 手动提交答案
→ 断言按替换后的 PaperInstanceItem 和评分依据判分
→ 班级统计只出现替换后考点
```

### 10.2 V1 与 V2

```text
发布 V1
→ 创建 V2 并发布，V1 被 superseded
→ 已存在的 V1 Submission 仍可查看和复核
→ 当前作业进度只统计 V2 当前目标
→ 历史区域显示 V1，不重复计算目标人数
```

M6 才测试真实图片上传；M5 先验证精确实例和版本统计。

### 10.3 重复与并发提交

```text
同一试卷相同幂等键重试
同一试卷不同请求并发
→ 最终只有一个 Submission
→ 每题只有一个 Attempt
→ 班级 confirmed_attempt_count 不重复
→ StudentMastery 不重复更新
```

### 10.4 历史改判

```text
确认错误
→ 记录班级正确率和个人 mastery
→ 改为正确
→ revision_no + 1
→ 班级正确率、个人画像和推荐预览同步变化
→ 全量 replay 与持久化 StudentMastery 一致
```

### 10.5 撤销确认

```text
teacher_confirmed
→ reopen 为 needs_review
→ 待复核数 + 1
→ confirmed_attempt_count - 1
→ 正确率分母移除该题
→ 个人画像全量重算
```

### 10.6 新学生与题库不足

- 新学生全部考点显示未评估，不出现 0 分或“薄弱”；
- 班级均值不因新学生加入而被按 0 拉低；
- 推荐候选不足时仍返回缺口和原因；
- 班级页提供“安排诊断”而不是断言能力差。

---

## 11. 前端工程质量

### 11.1 路由懒加载

使用 `React.lazy` 和 `Suspense` 按页面拆分至少以下路由组：

```text
analytics
questions
students
assignments / paper instances
grading
```

路由级 loading 使用稳定骨架屏。图表组件可以继续按需注册 ECharts 模块，禁止改为 `import * as echarts` 全量打包。

### 11.2 构建体积验收

记录拆分前后的：

```text
入口 JS 大小
analytics 路由 chunk
含 ECharts 的 chunk
构建是否仍有 >500 kB 默认提示
```

最低要求：

- `npm run build` 通过；
- 首屏不同步加载所有页面；
- 不能只调大 Vite 告警阈值；
- 若第三方共享 chunk 仍超阈值，在 M5 总结中记录组成和后续处理，不写“已优化”而没有数据。

### 11.3 响应式

至少验收：

```text
1440 × 1000
390 × 844
```

移动端要求：

- 无页面级横向滚动；
- 图表和数据表可读；
- 顶部卡片自动换行；
- 抽屉宽度不超过视口；
- 长考点名可换行或 Tooltip 展示完整名称；
- 关键按钮无需悬停即可理解。

### 11.4 可访问性与可解释性

- 图表容器提供准确 `aria-label`；
- 同一数据提供表格或列表替代；
- 不只用红绿颜色表达正确、错误和待复核；
- 键盘可以到达筛选、图表下钻替代按钮和抽屉关闭按钮；
- 百分比同时给出分子/分母；
- 时间显示时区，Tooltip 展示完整时间；
- 加载失败提供重试按钮。

---

## 12. 建议实施顺序

### 批次 A：冻结指标合同

1. 写清第 3、4 节口径；
2. 新增 `schemas/analytics.py`；
3. 先写纯聚合单元测试；
4. 固定 null、未评估、待复核和版本选择行为。

完成标志：给定小型夹具，可以准确手算并断言每个指标。

### 批次 B：班级聚合后端

1. 实现班级列表；
2. 复用画像上下文生成班级总览；
3. 实现考点学生下钻；
4. 实现 Assignment 进度；
5. 注册 API 和错误映射。

完成标志：Swagger 或 HTTP 测试能取得完整、稳定排序的响应。

### 批次 C：学情页面

1. 增加类型和 API 调用；
2. 完成筛选、总览、图表和数据表；
3. 完成学生下钻；
4. 接入作业进度和待处理链接；
5. 完成加载、空、错状态。

完成标志：老师能从班级页进入现有画像和批改证据。

### 批次 D：固定演示场景

1. 定义 `m5_start` 与 `m5_showcase`；
2. 实现场景准备脚本；
3. 建立隔离演示数据库重置方式；
4. 写场景预期摘要；
5. 连续从空库构造两次，结果一致。

完成标志：不修改业务代码即可重复得到同一演示状态。

### 批次 E：全链路质量

1. 补齐六类业务回归；
2. 新增 M5 Playwright；
3. 做路由懒加载和构建体积记录；
4. 检查 390px 页面；
5. 编写 `verify_m5.py`。

### 批次 F：演示交付

1. 更新 README；
2. 编写逐分钟演示脚本；
3. 保存验收命令和结果；
4. 编写 `M5_工作总结与M6交接.md`；
5. 明确 M6 可直接复用的接口和页面。

---

## 13. 测试矩阵

### 13.1 聚合单元测试

- 2 对 1 错得到 `accuracy = 0.6667`；
- 0 次确认作答得到 `accuracy = null`；
- `needs_review` 不进入正确率分母；
- 未评估学生不进入 mastery 均值；
- `insufficient` 有 mastery 但计入低证据量；
- 异常证据不进入统计，并增加异常数；
- 当前 revision 只计一次；
- 最近错误窗口与 M2 一致；
- 考点与学生排序稳定。

### 13.2 作业进度测试

- 未提交、待复核、已完成分类正确；
- 待复核计入提交率，不计入完成率；
- 草稿不进入有效发布分母；
- superseded/withdrawn 旧版不重复计入当前目标；
- 旧版已有提交仍可出现在历史统计；
- 同一学生出现两个 active published 时明确失败；
- 分母为 0 时 rate 为 null。

### 13.3 改判一致性测试

- 错改对后班级正确数 +1、错误数 -1；
- 对改错执行相反变化；
- reopen 后移出正确率分母并进入待复核；
- no-op 复核不改变统计；
- 陈旧 revision 请求 409，统计不变；
- 重新评分已有提交后仍只有一条学习证据。

### 13.4 API 测试

- 班级列表只包含未归档学生所属班级；
- 中文班级名 query 编码正确；
- 不存在的班级、考点和 Assignment 返回 404；
- chapter 不匹配返回 422；
- 响应中分子、分母和 null 语义完整；
- 前端不需要重新推导业务指标。

### 13.5 前端 E2E

- 默认进入学情页；
- 选择 `六年级 Demo 班` 后显示 5 名学生；
- 无证据考点显示“未评估”，不是 0%；
- 点击考点可查看学生明细；
- 点击学生进入个人画像；
- 待复核卡片进入带筛选的批改队列；
- 作业进度可进入作业详情；
- API 失败显示错误和重试；
- 桌面与 390px 均无页面级横向溢出；
- 图表存在可访问名称和数据表替代。

### 13.6 全流程回归

```text
空数据库迁移
→ 导入题库与 M1 历史
→ 准备 m5_start
→ 查看班级基线
→ 录入 / 判分 / 复核
→ 查看班级与个人画像变化
→ 再次组卷
→ 检查推荐理由变化
→ verify_m1～verify_m5 全部通过
```

---

## 14. `verify_m5.py` 设计

脚本应只读校验当前数据库，不修改数据，输出 JSON 并以非零退出码表示失败。

建议输出：

```json
{
  "schema_head": "0012",
  "class_name": "六年级 Demo 班",
  "student_count": 5,
  "exam_point_count": 13,
  "confirmed_attempt_count": 150,
  "pending_review_count": 0,
  "invalid_evidence_count": 0,
  "assignment_checks": 1,
  "duplicate_active_paper_targets": 0,
  "aggregate_mismatches": 0,
  "mastery_mismatches": 0,
  "validation_errors": 0,
  "errors": []
}
```

具体数量以场景配置和数据库为准，不把上面示例直接写死。脚本至少检查：

1. Alembic 当前为 head；
2. 班级聚合分子分母自洽；
3. `correct + incorrect = confirmed`；
4. `evidence + unassessed = student_count`；
5. null 只出现在无分母指标；
6. 班级聚合与逐学生 `load_context()` 独立重算一致；
7. StudentMastery 与 replay 一致；
8. 一个 Assignment × Student 最多一个 active published 目标；
9. 当前 Attempt 修订存在；
10. 没有把 `needs_review` 计入画像或正确率。

`verify_m5.py` 不应通过调用班级聚合服务后再原样比较自身结果；至少一组关键计数要从底层有效证据独立重算，否则无法发现服务自身的错误。

---

## 15. 完成实现后的命令

### 15.1 后端

```powershell
cd code\backend
alembic upgrade head
python -m pytest
python scripts/verify_m1.py
python scripts/verify_m2.py
python scripts/verify_m3.py
python scripts/verify_m4.py
python scripts/verify_m5.py
```

### 15.2 前端

```powershell
cd code\frontend
npm run typecheck
npm run build
npm run test:e2e
```

建议把 M5 E2E 单独标记，以便快速回归：

```powershell
npx playwright test tests/m5.spec.ts
```

### 15.3 空库演示环境

演示环境的准确命令应在实现脚本后写入 README，顺序必须保持：

```text
启动隔离 PostgreSQL
→ alembic upgrade head
→ seed_database.py
→ rebuild_mastery.py
→ prepare_m5_demo.py --scenario m5_start
→ verify_m1.py ... verify_m5.py
→ 启动后端与前端
```

不能只在已有开发库中验证成功。

---

## 16. 手工演示脚本

### 16.1 建议的 8～10 分钟路径

1. 打开“班级学情”，说明当前为 5 名虚拟学生；
2. 指出正确率、mastery 和证据覆盖的区别；
3. 点击一个需关注考点，查看不同学生的证据状态；
4. 进入 S001 画像，点击曲线点打开具体作答证据；
5. 返回班级页，打开当前作业进度；
6. 进入待复核试卷，完成一次确认或改判；
7. 回到班级页，刷新并展示分子、分母和 mastery 变化；
8. 进入该学生推荐练习，说明推荐分项和证据；
9. 创建下一份个性化作业，展示学生间选题差异；
10. 总结 M6 将只增加图片答案提取，继续复用当前判分与审计链路。

### 16.2 必须主动说明的限制

- 数据是虚拟演示数据；
- mastery 是透明规则产生的组卷参考值，不是标准化能力测评分；
- 当前没有教师核对的能力映射，因此不展示能力雷达；
- 当前批改以手动录入为主；
- 图片识别、打印答题区和真实样本效果评估在 M6；
- 复杂代数等价和步骤给分尚未实现。

### 16.3 现场故障兜底

演示前准备：

```text
隔离数据库的 m5_start 状态
隔离数据库的 m5_showcase 状态
verify_m1～verify_m5 输出
一份录屏或关键页面截图
README 中的手动启动命令
```

核心闭环不依赖外部模型或网络。前端异常时可以通过 API 文档说明数据；演示状态损坏时切换到重新初始化的隔离数据库，不在现场修改审计记录。

---

## 17. M5 完成后应提交的产物

### 17.1 后端

- 班级、考点学生和作业进度聚合 API；
- 明确的 analytics schemas 与服务层；
- 固定演示场景准备服务；
- `verify_m5.py`；
- M5 单元、API 和工作流回归测试。

### 17.2 前端

- `/analytics` 班级学情页；
- 正确率、mastery 和证据覆盖视图；
- 考点学生下钻；
- 作业进度与待处理入口；
- 路由懒加载；
- 完整加载态、空态、错态、移动端和无障碍替代内容；
- `tests/m5.spec.ts`。

### 17.3 数据与脚本

- `m5_demo_scenarios.json`；
- `prepare_m5_demo.py`；
- 安全的隔离演示环境重置脚本或等价流程；
- 固定场景的预期摘要和校验结果。

### 17.4 文档

- README 的 M5 启动、重置、测试和演示说明；
- 构建体积前后对比；
- 8～10 分钟演示脚本；
- `code/docs/12_M5_工作总结与M6交接.md`；
- 已知限制和未解决问题。

### 17.5 给 M6 的稳定输入

M6 应直接复用：

```text
精确 instance_code / paper_instance_id 定位
PaperInstanceItem 发布快照
ManualAnswerIn 的题号、题目 ID 和答案合同
M4 grade_answer 判分入口
needs_review 与教师复核页面
AttemptRevision 追加式审计
画像事务重算
M5 班级与作业聚合
固定演示数据库和全链路回归
```

M6 图片识别完成后，班级学情不应修改指标定义，只会多一种答案来源和更多待复核状态。

---

## 18. 常见返工点

### 18.1 把未评估算成 0

这会把新学生错误展示为能力最弱，并拉低班级均值。无证据必须用 `null` 和“未评估”。

### 18.2 把待复核算错题

这会在教师确认前污染正确率和画像。`needs_review` 只能进入待处理数量。

### 18.3 平均每个学生的正确率

`平均(学生正确率)` 与 `总正确次数 / 总确认次数` 含义不同。M5 班级考点正确率固定使用后者，并同时返回人数覆盖；不要在不同页面混用。

### 18.4 用当前题库考点覆盖历史快照

历史聚合必须读取 PaperInstanceItem 快照，否则题库审核会使过去的班级统计漂移。

### 18.5 旧版和新版同时计入作业目标

同一学生的 V1、V2 不能同时增加当前作业分母。旧版只作为历史保留。

### 18.6 前端自己聚合

前端拼接学生画像和批改队列很容易遗漏异常证据、当前修订和版本状态。所有业务口径由后端输出。

### 18.7 为演示直接改数据库

直接插 Attempt 或删除 revision 会绕过 M4 事务与审计。固定场景必须调用服务；重置使用隔离数据库。

### 18.8 强行绘制能力雷达

没有教师确认映射时，漂亮的雷达图反而降低可信度。先展示有证据的考点事实。

### 18.9 只提供图，不提供数据表

图表 Tooltip 不利于移动端、键盘和精确核对。必须保留分子/分母和表格替代。

### 18.10 通过提高阈值隐藏包体积警告

M5 应做真实路由拆分，并记录构建数据；调高告警阈值不算优化。

### 18.11 用当前时间生成固定场景

这会让推荐排序和“最近”指标每天变化。固定场景中的业务时间必须显式给定。

### 18.12 只测展示，不测改判后的变化

M5 的价值不只是静态大屏。必须证明提交、复核、撤销和历史改判会以同一证据语义更新个人与班级视图。

---

## 19. M5 最终验收清单

### 指标与数据

```text
□ 班级范围只包含未归档学生。
□ 正确率只统计有效确认作答。
□ needs_review 不进入正确率和 mastery。
□ 未评估返回 null，不使用 0 或 0.5 展示。
□ 班级 mastery 均值同时展示证据学生数。
□ 聚合使用发布快照主考点和当前 revision。
□ 当前作业目标不重复计算旧版本。
□ 班级聚合与逐学生 replay 一致。
```

### 页面

```text
□ 有班级学情总览、考点图表、学生下钻和作业进度。
□ 能从统计值进入学生画像和具体 Attempt 证据。
□ 待复核和未提交有直接处理入口。
□ 正确率、mastery、证据量的文案不混淆。
□ 无映射时不展示能力雷达。
□ 空态、错误态、重试和加载态完整。
□ 1440px 与 390px 均可用且无页面级横向溢出。
□ 图表有 aria-label 和数据表替代。
```

### 固定演示

```text
□ 可从空的隔离数据库构造 m5_start。
□ 可从空的隔离数据库构造 m5_showcase。
□ 场景不依赖数据库 UUID 和系统当前时间。
□ 场景构造调用正式业务服务。
□ 重置不会删除默认开发库或放松 revision 守卫。
□ 演示脚本可在 8～10 分钟内完成。
```

### 测试与工程质量

```text
□ 后端全部 pytest 通过。
□ verify_m1.py～verify_m5.py 均为 0 个错误。
□ 前端 typecheck 和 production build 通过。
□ M5 Playwright 桌面与移动端通过。
□ 发布前替题、版本、重复提交、改判、撤销和新学生场景已自动化。
□ 路由已经懒加载，构建体积变化有记录。
□ README 和 M5→M6 交接文档已更新。
```

---

## 20. 现在最先做的一件事

先不要画班级大屏。第一步新增 `schemas/analytics.py` 和 `tests/test_class_analytics.py`，用 3 名学生、2 个考点的小夹具固定以下四个最容易出错的口径：

```text
1. needs_review 不进入正确率分母；
2. 未评估学生不按 0 或 0.5 进入 mastery 均值；
3. 当前 revision 只计一次；
4. superseded 旧试卷不重复增加当前作业目标。
```

这四项通过后，再实现聚合服务和页面。这样 M5 后续的每张图都有明确、可测试、可追溯的数据含义。
