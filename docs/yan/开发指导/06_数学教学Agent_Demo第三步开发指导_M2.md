# 数学教学 Agent Demo 第三步开发指导：M2 学生画像与规则推荐基础

> 编写日期：2026-09-23  
> 前置阶段：M0、M1 已完成。本文是 M2 实施指导，不表示 M2 已实现。  
> 核心依据：[开发步骤规划](03_数学教学Agent_Demo开发步骤规划.md)第 5 节、[技术设计方案](02_数学教学Agent_Demo技术设计方案.md)第 8 至 11 节，以及 [M1 工作总结与 M2 交接](code/docs/05_M1_工作总结与M2交接.md)。  
> 适用工程：当前目录下的 `code/`，继续使用已有 React、FastAPI、SQLAlchemy、Alembic 和 PostgreSQL 工程。

---

## 1. M2 要交付什么

M2 的目标是：**把 M1 的历史作答转化为可解释、可重算、可追溯的学生画像，并提供有教学范围约束的规则推荐预览。**

完成后，打开一名学生的详情，应能回答：

1. 哪些考点已有作答证据，哪些尚未评估？
2. 每个考点的掌握度参考值是多少，依据了多少次作答、多少道不同题？
3. 最近哪些题答错了，哪些考点值得继续练习？
4. 掌握度怎样变化，每一步对应哪次作答和哪个修订版本？
5. 在教师指定的范围内，建议练哪些题，推荐原因是什么？

本阶段的完整链路：

```text
当前有效 Attempt + 已发布试卷题目快照
→ 按学生、主考点分组，稳定排序
→ 从 0.5 重放 EMA
→ 生成考点汇总、变化曲线、证据列表
→ 展示学生画像
→ 教师设置练习范围
→ 候选过滤、打分、约束选题
→ 返回推荐预览、理由或题量缺口
```

### 1.1 阶段边界

| M2 完成 | 后续阶段完成 |
|---|---|
| 画像纯计算、显式重算脚本、StudentMastery 汇总 | M4 提交、教师改判与画像重算的同事务接入 |
| 考点条形图、单考点变化曲线、证据回溯 | M5 班级学情与完整演示打磨 |
| 规则推荐服务、候选题预览、原因与缺口 | M3 创建 Assignment、生成试卷、换题、调序和发布 |
| 已有整题正确 / 错误结论的消费 | M4 答案判分、数学表达式解析 |
| 无作答和低证据量状态 | 后续经过验证的能力测评模型 |

M2 不引入 LLM、向量数据库、复杂 RAG 或多 Agent。当前阶段使用的知识基础就是已有的结构化题库、知识点、考点和题目关联。图表优先展示考点，不要求补齐能力雷达图。

开发规划第 5.3 节已经列出规则推荐要求，因此本文将其落实为**不写入作业或试卷的推荐预览**；M3 复用相同服务完成正式组卷，避免 M2 只有画像数字而无法验收推荐理由。

## 2. 当前基础与开工检查

### 2.1 已有成果

以下基线来自 M1 交接记录与现有代码，开工时用脚本核实本地数据库：

| 项目 | 当前基础 | M2 使用方式 |
|---|---|---|
| 正式题库 | 30 组，20 组填空、10 组选择 | 推荐候选池，不扩充其他章节 |
| 知识结构 | 54 个考点，种子题覆盖其中 10 个主考点 | 区分知识树范围、题库覆盖和已评估范围 |
| 虚拟学生 | S001 至 S005，共 5 名 | 画像演示对象 |
| 历史数据 | 3 次作业、15 份已发布试卷、150 条确认作答 | 每名学生 30 次整题作答 |
| StudentMastery | 表已建立，M1 未写入画像 | 重放后填充，无需重新建表 |
| 迁移 | 0001 至 0004 | 优先沿用，不修改旧迁移 |
| 前端 | 学生详情与历史作答页 | 在已有页面上增加画像和推荐视图 |

**54 个考点不等于 54 个考点均已评估。**在原始 M1 种子状态下，预计生成 5 × 10 = 50 条有证据的 StudentMastery；其余考点在查询时补为“未评估”，不为了凑齐数据而写入 0.5。

### 2.2 优先阅读的文件

以下路径均相对 `code/`：

| 文件 | 阅读目的 |
|---|---|
| `backend/app/models/entities.py` | 实际字段、主键、唯一约束与快照结构 |
| `backend/app/services/seed_importer.py` | 历史批次、模拟正确性与快照写入方式 |
| `backend/app/api/routes/students.py` | 学生和作答接口接入位置 |
| `backend/app/schemas/students.py` | 当前响应结构及 M1 占位文案 |
| `backend/scripts/verify_m1.py` | 已有验收方式 |
| `frontend/src/pages/StudentDetailPage.tsx` | 画像页面承载位置 |
| `frontend/src/types/index.ts`、`frontend/src/api/client.ts` | 类型和请求封装 |
| `README.md` | 当前电脑启动方式与数据库端口 |

### 2.3 先核实环境，不重建环境

按 `code/README.md` 启动已有本地 PostgreSQL。当前电脑使用项目内 `.conda-env`，数据库端口为 `55432`。已初始化的数据库不需要重新执行 `initdb` 或种子导入。

在 PowerShell 运行：

```powershell
Set-Location 'D:\desktop\math-agent\code\backend'
$env:DATABASE_URL = 'postgresql+psycopg://postgres@localhost:55432/math_agent'
& '..\.conda-env\python.exe' -m scripts.verify_m1
& '..\.conda-env\python.exe' -m pytest -q
```

记录开工基线；数据库无法连接时先恢复服务，不为绕过连接问题切换 SQLite。M1 的 Compose 完整镜像拉取曾受网络影响，M2 不应以重新下载全部镜像为前提。

## 3. 先固定数据口径

### 3.1 什么是一次有效作答

参与画像的记录必须同时满足：

```text
grade_status 属于 auto_confirmed / teacher_confirmed
is_correct 明确为 true 或 false
answered_at 有值
能够定位 PaperInstanceItem 及其主考点快照
学生、Submission、PaperInstance 和题目归属一致
```

`needs_review` 不参与画像。已确认但 `is_correct=null` 属于数据异常，也不参与，并进入数据质量报告；不能把 null 转成 false。现有数据库约束只保证待复核时结果为空，并未保证所有确认状态都有布尔结果，因此读取层仍需检查。

一个 `Attempt` 表示一个试卷题目的当前结论；`AttemptRevision` 是修订日志，不是额外学习次数。重放只消费当前 Attempt，每个曲线点带上当前 `revision_no`，需要查看修订详情时再取对应日志。

同一道题在新的真实练习中可以再次计数，但同一 Attempt 的重复上传、重复查询或修订不能增加次数。多空题组仍按一个 Attempt 计数，直接使用整题 `is_correct`，不在 M2 拆空计分。

### 3.2 主考点必须来自历史快照

M1 的实际写入结构为：

```python
exam_point_snapshot = {
    "main": {"id": exam_point.code, "name": exam_point.name},
    "related": [],
}
```

这里的 `main.id` 是**稳定语义编码**，不是数据库 UUID。正确映射过程是：

```text
Attempt.paper_instance_item_id
→ PaperInstanceItem.exam_point_snapshot.main.id
→ ExamPoint.code
→ ExamPoint.id（写入 StudentMastery.exam_point_id）
```

不要直接把 `main.id` 转成 UUID，也不要通过当前 `QuestionExamPoint` 反查后覆盖历史归属。批量建立 `code → ExamPoint` 映射，避免每条作答都发起一次查询。

证据详情的题干、难度、选项、标准答案及判分规则同样来自 PaperInstanceItem 快照。当前题库详情可作为另一个入口，但不能代替“当时做的题”。

快照缺失、编码无法映射或归属不一致时：查询返回被排除的记录数和原因；验收失败；重算脚本在写入前报错并回滚该学生的重算。不要退回当前题库标签猜测历史考点。

### 3.3 当前接口需要注意的差异

现有 `students.py` 的历史接口使用当前题库 `QuestionExamPoint` 获取考点，尚未返回 `revision_no`。建议 M2 将历史题干、难度和主考点改为从快照读取，并为响应补充：

```text
revision_no
used_in_profile
exclusion_reason
```

`exam_point_code` 筛选及总数查询也要同步改为快照口径，不能只改返回字段。保留已有 `question_id` 和 `original_qid`，维持题库跳转兼容。

学生列表已有的 `attempt_count` 是历史记录总数。保留这个含义，画像另用 `valid_attempt_count`；待复核记录出现后，这两个数允许不同，界面名称应明确。

### 3.4 指标定义

| 指标 | M2 口径 |
|---|---|
| `attempt_count` | 该学生在该主考点的有效 Attempt 数 |
| `correct_count` | 上述记录中 `is_correct=true` 的数量 |
| `distinct_question_count` | 上述记录去重后的 `question_id` 数量 |
| `recent_attempt_count` | 该考点最后最多 5 次有效作答的数量 |
| `recent_wrong_count` | 上述最近窗口中答错的数量 |
| `last_practiced_at` | 最后一次有效作答的 `answered_at`，无证据为 null |
| `mastery` | 从初值 0.5 重放得到的 EMA，不是正确率 |
| `confidence_level` | 按有效作答次数分档的证据量，不是概率 |
| 练习范围 | 实际出现过的题型、难度和作答时间范围，依据历史快照 |

“最近 5 次”是本文补充的 **M2 建议默认值**，不是原规划已经指定的窗口。将其集中定义为 `RECENT_WINDOW_SIZE = 5`。不足 5 次就使用实际次数，界面写“最近 N 次错 W 次”，不能写成“最近 5 次”或“最近 5 天”。

`distinct_question_count`、练习范围与曲线可按需计算，无需先增加数据库列。不要把不同考点的正确率平均值当成整体能力。

## 4. 实现可重放的画像计算

### 4.1 拆成三个清晰职责

建议新增以下文件，名称可按项目习惯调整：

| 文件 | 职责 |
|---|---|
| `backend/app/services/mastery.py` | 纯计算、EMA、分档、最近窗口和曲线 |
| `backend/app/services/student_profile.py` | 查询证据、校验快照、调用重放、组织响应和汇总写入 |
| `backend/app/schemas/profile.py` | 考点画像、曲线点、证据及接口响应类型 |
| `backend/scripts/rebuild_mastery.py` | 显式重建一个或全部学生的汇总 |

纯计算函数不访问数据库、不读取系统时间、不提交事务，也不改写输入。输入可使用 dataclass 或已有 Pydantic 风格的明确结构，不需要新增通用规则引擎。

每条输入至少包含：

```text
attempt_id、student_id、question_id、paper_instance_item_id
exam_point_id、exam_point_code
answered_at、revision_no、grade_status、is_correct
历史题型和难度
```

### 4.2 重放过程

1. 校验输入并过滤非有效记录。
2. 按 `(student_id, exam_point_id)` 分组。
3. 每组按 `(answered_at, attempt_id)` 升序排序。
4. 每组从 0.5 开始逐条计算，同时生成曲线点。
5. 从同一批有效记录计算计数、最近窗口、不同题目数和练习范围。
6. 返回汇总、曲线和数据质量信息。

统一使用带时区时间，后端比较按 UTC，页面按本地时区显示。同一时刻的 UUID 排序应与 PostgreSQL UUID 顺序一致，不依赖数据库未声明的默认返回顺序。

EMA 公式沿用规划：

```text
alpha = 0.2
mastery_next = 0.8 × mastery_previous + 0.2 × correctness
correctness = 1（正确）或 0（错误）
```

手算基准：

| 顺序 | 结果 | 更新前 | 更新后 |
|---|---|---:|---:|
| 初始 | 尚无作答 | - | 0.5000 |
| 1 | 正确 | 0.5000 | 0.6000 |
| 2 | 错误 | 0.6000 | 0.4800 |
| 3 | 正确 | 0.4800 | 0.5840 |

此时 `attempt_count=3`、`correct_count=2`、`recent_wrong_count=1`、证据量为 `medium`。正确率是 2/3，掌握度参考值是 0.584，二者不是同一指标。

当前数据库为 `Numeric(5,4)`。建议计算采用 Decimal，中间步骤不按四位截断，落库及 API 展示统一四舍五入到四位；不能每一步都读回已舍入的缓存继续累加。测试明确使用相同量化规则，避免曲线末点与汇总差一位。

### 4.3 曲线必须保留证据标识

每个真实作答点至少返回：

```text
attempt_id
revision_no
answered_at
question_id
paper_instance_item_id
is_correct
mastery_before
mastery_after
```

无作答时曲线为空。内部初值 0.5 不伪装为一次真实测量；如图中展示初始参考线，应与作答点区分。

改判重放示例：把第二次作答从错误改为正确，并将该 Attempt 的 `revision_no` 从 1 改为 2，则完整序列变为：

```text
0.5 → 0.6 → 0.68 → 0.744
```

有效次数仍为 3。改判反映在原作答位置，不用 `reviewed_at` 把它挪到今天，也不在末尾额外追加一个学习点。M2 用纯函数和隔离测试验证这种行为，实际改判接口留给 M4。

### 4.4 冷启动与薄弱点

以下证据量阈值是规划已确定的规则：

| 有效次数 | `confidence_level` | 页面状态 |
|---:|---|---|
| 0 | low | 未评估，不显示 50% |
| 1 至 2 | low | 证据较少，可显示参考值，不做确定性薄弱判断 |
| 3 至 7 | medium | 显示考点表现及支持范围 |
| 8 及以上 | high | 证据数量较多，仍展示不同题目数和练习范围 |

建议接口使用 `assessment_status=unassessed/insufficient/assessed`，与 `confidence_level` 分开。零次作答对外 `mastery=null`，内部算法仍从 0.5 起算。

薄弱点展示的建议默认规则：

- 有效次数至少 3 次、不同题目至少 2 道，且 mastery < 0.4：列入“建议重点练习”。
- 有效次数至少 3 次、不同题目至少 2 道，且最近窗口错误率至少 0.5：列入“近期表现需关注”。
- 仅 1 至 2 次证据：列入“建议继续观察”，可描述实际错题，不标成“明显薄弱”。
- 无有效作答：列入“待诊断”，不进入薄弱点排名。

其中“至少 2 道不同题”和窗口错误率阈值是本文的保守实现建议，不是经过校准的测评标准。重复练少数题即便次数很多，也不能据此声称已全面掌握。

理由用可核实的模板，例如：“该考点最近 3 次确认作答中有 2 次错误，涉及 3 道不同题，建议继续练习。”附支持的 Attempt 标识。没有解题步骤证据时，不断言学生“忘了负号”或“逻辑能力差”。

### 4.5 StudentMastery 的写入与读取

`StudentMastery` 是可重建汇总，Attempt 与快照才是证据来源。建议 M2 采用以下简单方案：

1. `rebuild_mastery` 在显式执行时写入汇总。
2. 每次重算一个学生的全部有效历史，从 0.5 重放，不以已有 mastery 为起点。
3. 在一个事务内按联合主键更新或插入；清除该学生已经没有有效证据的旧汇总行。
4. 重算异常时回滚，不删除其他学生的汇总，不触碰 Attempt、修订日志和发布快照。
5. M2 的画像和推荐查询使用同一重放服务按需计算当前结果，GET 不写数据库；这样即便尚未运行重算脚本，也不会把“未初始化缓存”误认为“未作答”。

同一输入连续重算两次，业务数值、行数和证据顺序应一致；运行时间不属于业务结果。建议仅在汇总字段变化时更新已有行，避免无意义刷新 `updated_at`。

M2 数据量只有 150 条，按需重放足够。M4 再把“更新 Attempt、追加 Revision、重算 StudentMastery”接入同一事务和学生级串行处理；汇总写入函数接受调用方 Session，不在内部擅自 commit，便于复用。

## 5. 实现规则推荐预览

### 5.1 先把参数写成契约

新增 `backend/app/services/recommendations.py` 与对应 Schema，输入建议如下：

| 参数 | 约束与含义 |
|---|---|
| `chapter_code` | 本阶段只接受当前有理数章节编码 |
| `new_exam_point_codes` | 教师指定的新知识范围 |
| `review_exam_point_codes` | 教师指定的已学回顾范围 |
| `new_count`、`review_count` | 非负整数，合计至少 1；M2 建议总量上限 30 |
| `question_types` | choice / fill_blank 的非空子集 |
| `base_difficulty` | 教师给定基础难度，整数 1 至 5 |
| `min_difficulty`、`max_difficulty` | 明确允许的难度范围，基础难度应在范围内 |
| `avoid_recent_days` | 避免重复的天数，建议默认 7，0 表示显式关闭 |
| `max_exam_point_ratio` | 单个主考点最大占比，建议默认 0.5，取值 (0,1] |

两个考点集合不重叠，非零题量必须有对应范围；传入不存在或不属于本章节的考点时返回参数错误，不忽略错误编码。采用分项题量可以直接表达新旧比例，例如 4 道新知识 + 2 道回顾；M3 再提供比例输入的交互。

不要用知识树全部 54 个考点自动推断“已学范围”。M1 的 `Assignment.teaching_scope` 只有演示批次信息，不足以充当新旧知识配置，推荐预览需重新由教师选择。

服务内部接受显式 `as_of`，同一请求的画像、近期重复排除和时间分值共用这个时间。线上预览由后端取当前 UTC 时间一次并返回，测试传入固定时间，不在多个函数内反复调用 now。

### 5.2 候选过滤

依次筛选并记录每一步剩余数：

1. 当前章节内的正式题库题目。
2. `source_review_status=reviewed` 且 `grading_readiness=machine_ready`。
3. 当前主考点属于本次对应的新知识或回顾范围。
4. 题型和难度在教师指定范围内。
5. 排除该学生在最近 `avoid_recent_days` 内已作答的相同 `question_id`。

第 5 步可以读取所有当前 Attempt，包括待复核记录，因为它们已发生题目接触；这些记录仍不参与画像。时间窗口明确为 `[as_of - days, as_of]`，0 天时跳过此过滤。

画像读取历史快照；推荐新练习则使用当前题库标签与当前题目内容。这两种用途不要混用。M2 先按主考点约束范围，关联考点可展示为辅助信息，不用它绕过主考点范围限制。

### 5.3 打分的建议具体口径

对有至少 3 次有效作答且至少 2 道不同题的考点，使用规划给出的权重：

```text
score = 0.40 × weakness
      + 0.25 × recent_wrong
      + 0.20 × difficulty_match
      + 0.15 × forgetting
```

| 分项 | 建议公式 | 解释 |
|---|---|---|
| weakness | `1 - mastery` | 当前考点的练习优先程度 |
| recent_wrong | `recent_wrong_count / recent_attempt_count`，分母为 0 时取 0 | 最近最多 5 次中的错误比例 |
| difficulty_match | `max(0, 1 - abs(difficulty - target_difficulty) / 4)` | 在 1 至 5 档难度尺度上归一化 |
| forgetting | `min(max(elapsed_days, 0) / 30, 1)` | 仅回顾题启用；新知识题取 0 |

`elapsed_days` 来自该考点最后一次有效练习到 `as_of` 的时间差。这里 30 天是建议参数，字段虽沿用规划名称 `forgetting`，界面应称“回顾间隔”，不能宣称已遗忘。时间因素只改变推荐优先级，不自动降低 mastery。

目标难度建议：mastery < 0.4 时为 2；0.4 至 0.7 时为 3；> 0.7 时为 4，再限制在教师允许范围内。这是 Demo 的选题策略，不是题目难度测量模型。

**冷启动分支：**不足 3 次有效作答或不足 2 道不同题时，不用 mastery 推断目标难度。令 `target_difficulty=base_difficulty`，并将 weakness、recent_wrong、forgetting 设为 0，仅按难度匹配排序，理由标为“基础诊断 / 补充证据”。原始错题统计仍可展示。

此分支是对规划冷启动要求的具体落实。返回 `strategy=diagnostic/personalized`，不能把低证据量的内部 0.5 套进普通 weakness 公式。冷启动题分数较低不代表学生更强；score 只表示本次参数下的题目优先级。

### 5.4 约束选择与缺口

同一输入应得到稳定结果。M2 可直接按 `score` 降序、`original_qid` 升序、`question_id` 升序排序，然后在两个范围内分别选取，满足题量和考点上限。这是可复现的约束选择，无需添加随机抽样。

考点上限建议定义为：

```text
total_requested = new_count + review_count
max_per_exam_point = floor(total_requested × max_exam_point_ratio)
```

如果上限小于 1，返回参数错误并提示提高比例；不要自动改为 1 而突破严格占比。比例分母固定为请求题量，响应同时展示请求数、实际返回数和每考点数量，避免少题时误读比例。

选题时同一 `question_id` 只能出现一次。范围集合不重叠，因此新知识与回顾不会争用同一主考点配额；任一类别不足，不把另一类别的题静默挪过来补齐。

不足时返回 HTTP 200 的有效预览结果：

```text
status = insufficient_candidates
requested_count、selected_count、shortfall
new_shortfall、review_shortfall
各过滤阶段的数量
缺题范围、受限考点及原因
```

原因使用稳定编码，例如 `no_questions_in_scope`、`difficulty_filter`、`recently_practiced`、`exam_point_cap`。按固定过滤顺序报告流失数量，不把各项独立排除数相加误称为总缺口。

无候选、只选出部分题都属于正常业务状态。不要超教学范围、不重复填充，也不调用模型生成题目。教师可以显式修改参数后重新预览。

### 5.5 可解释输出

每道推荐题至少返回：

```text
question_id、original_qid、主考点、题型、难度
bucket（new / review）、strategy
score、四项原始分值、权重和加权贡献
target_difficulty
reason_codes、reason_text
支持该理由的 attempt_ids 及 revision_no
```

顶层返回 `as_of`、`rule_version`（建议 `m2-v1`）和实际使用的参数。冷启动理由可以没有证据 ID，但必须明确“证据不足”；范围限制与基础难度等理由应指向教师参数，不能伪造支持作答。

示例：“回顾范围内，该考点最近 3 次确认作答有 2 次错误；本题难度 2，与当前目标难度一致。”这类模板已足够，不需要 LLM 改写。

## 6. API 接入方案

沿用 `/api/v1` 前缀，建议实现以下接口：

| 接口 | 用途 |
|---|---|
| `GET /students/{student_id}/mastery` | 考点画像列表，可按章节、考点筛选 |
| `GET /students/{student_id}/profile` | 画像摘要、最近错误、需关注考点与待诊断考点 |
| `GET /students/{student_id}/mastery/{exam_point_code}/history` | 单考点曲线和对应证据摘要 |
| `GET /students/{student_id}/attempts/{attempt_id}` | 历史题目快照、当前作答和对应修订信息 |
| `POST /students/{student_id}/recommendations/preview` | 接收范围参数，返回推荐预览；不写作业或试卷 |
| `GET /exam-points?chapter_code=...` | 若现有接口无法提供范围选择数据，新增轻量考点目录接口 |

路由中的 `student_id`、`attempt_id` 是数据库 UUID；`S001` 是显示编码，不是 UUID。`exam_point_code` 则使用语义编码。

考点目录应返回名称、编码、章节及当前可用题数，可以展示“暂无可用题”。保留无候选考点的信息，教师才能理解范围与题库覆盖之间的差异。

### 6.1 响应中的关键区分

`mastery` 的每项包含：

```text
exam_point_id、exam_point_code、exam_point_name
assessment_status、mastery、confidence_level
attempt_count、correct_count、distinct_question_count
recent_attempt_count、recent_wrong_count、last_practiced_at
practiced_question_types、practiced_difficulties
available_question_count
```

`profile` 的摘要区分：

```text
total_attempt_count：所有当前作答记录数
valid_attempt_count：参与画像的数量
pending_review_count：待复核数量
invalid_evidence_count：异常证据数量
assessed_exam_point_count：至少 1 次有效作答的考点数
unassessed_exam_point_count：范围内没有有效作答的考点数
```

这里 `assessed_exam_point_count` 表示“已有证据”，包含 `assessment_status=insufficient` 的考点；页面命名为“已有证据考点”。如需统计证据达到阈值的考点，另设字段，避免与状态枚举混淆。

画像摘要、最近错误和曲线应来自同一次证据读取与相同规则。建议 `/profile` 同时带画像列表，页面无需并发请求两个接口拼接可能不一致的结果；独立 `/mastery` 供其他页面或 M3 使用。

未知学生返回 404；学生存在但无作答返回正常空画像；不属于该学生的 Attempt 返回 404；非法范围和参数返回既有风格的 422。保持项目现有结构化错误格式。

### 6.2 避免证据接口的假分页

不能只读取历史接口前 100 条就计算画像。画像服务查询完整有效历史；证据表分页只是显示方式。曲线在当前小数据量下可返回完整序列，后续规模扩大再增加明确的时间筛选。

已有 `StudentDetailPage.tsx` 将接口前 100 条记录在客户端分页，M2 增加历史筛选时应接入服务端 `page/page_size/total`，不能让教师误以为已看到完整历史。

## 7. 前端开发

### 7.1 在学生详情页增加视图

建议使用“画像 / 历史作答 / 推荐练习”三个标签页，保留学生基本信息和演示数据标识。

画像视图从上到下包含：

1. 有效作答数、不同题目数、已有证据考点数和待复核数。
2. 默认展示最多 5 个重点考点的横向条形图和简要诊断依据。排序优先使用现有关注项类别，再参考近期错误比例和掌握度参考值；不足 5 个时用已有证据考点补足。
3. 全页只保留一张选中考点的变化曲线。点击重点柱或考点名称切换曲线，点击真实作答点打开证据抽屉；逐次作答表默认折叠。
4. 全部考点表、未评估状态和最近错误放入“完整画像与作答证据”折叠区，仍能筛选“已有证据 / 全部”，但不占据默认首屏。

考点名称较长时优先横向条形图，给名称换行或悬浮完整显示。未评估项显示文字或空值，不画成 0 分柱或 50% 柱。使用 0 至 1 参考刻度，避免标成“能力百分比”。

画像采用渐进展示：默认层用于快速判断“当前最值得关注什么”，趋势层用于解释“为什么变化”，完整层用于审计全部证据。M2 不为每个考点同时绘制独立图表，也不在本次界面调整中提前实现 M3 的正式组卷、教师调题或发布能力。

证据抽屉展示原作答时间、题目快照、学生答案、确认状态、正确性和修订号，并复用 `RichQuestionText`、`AnswerView` 展示资源和多空答案。

### 7.2 图表和请求接入

当前 `frontend/package.json` 尚未安装 ECharts。实施时增加 `echarts` 依赖并更新锁文件；建议封装小型图表组件，完成初始化、数据更新、容器 resize 和销毁即可，不必引入新的状态管理框架。

建议新增组件：

```text
frontend/src/components/MasteryBarChart.tsx
frontend/src/components/MasteryHistoryChart.tsx
frontend/src/components/AttemptEvidenceDrawer.tsx
frontend/src/components/RecommendationPreview.tsx
```

现有 API 客户端只有 `apiGet`，推荐预览需补充 POST JSON 请求能力并沿用错误处理。所有响应增加 TypeScript 类型；分数、理由和薄弱点由后端计算，前端只格式化。

后端 `app/main.py` 当前 CORS 的 `allow_methods` 只有 `GET`、`OPTIONS`。接入预览接口时加入 `POST`，保留既有允许来源配置，并在浏览器验证 JSON 请求的 OPTIONS 预检和实际 POST 均成功；仅后端 TestClient 通过不能证明跨域调用可用。

切换学生、考点或推荐参数时清理旧结果，并使用 AbortController 或请求标识避免旧响应覆盖新页面。组件应包含加载、无证据、请求失败、资源失败和候选不足状态。

### 7.3 推荐预览交互

教师在当前学生页配置新知识和回顾范围、题量、题型、难度、避免重复天数和考点上限，点击“预览推荐”。结果表显示题目、范围类别、主考点、难度及理由，可展开打分详情或跳转题库。

缺题时显示请求题量、实际题量、缺口和主要限制。M2 不提供“发布作业”按钮，也不创建看似正式试卷的编号。

必要的指标解释保持简短：“作答证据量按确认作答次数分档，并非统计置信概率；掌握度仅作教学参考。”更新 Schema 中“将在 M2 计算”的旧文案。

## 8. 建议实施顺序

每一步完成后检查退出条件，再进入下一步。

| 步骤 | 工作内容 | 退出条件 |
|---|---|---|
| M2-1 | 整理有效作答输入和快照编码映射 | 能从 S001 读取 30 条可追溯证据，归属无误 |
| M2-2 | 实现纯重放函数及关键单测 | 手算 EMA、同时间排序、待复核排除、改判与撤销通过 |
| M2-3 | 实现汇总写入和重算脚本 | 5 人重算成功，二次执行业务结果一致，不新增作答 |
| M2-4 | 实现画像、曲线和证据接口 | 画像每个结论可定位到 Attempt 和 revision_no |
| M2-5 | 实现推荐服务和预览接口 | 范围、去重、占比、冷启动、理由和缺口均有效 |
| M2-6 | 接入详情页、图表和推荐视图 | 从学生到考点再到历史证据可连续操作 |
| M2-7 | 自动验收、回归、更新 README 与交接 | 测试通过，实际结果和限制记录完整 |

第一条建议跑通的纵向链路是：**S001 → 一个主考点 → 重放结果 → API → 条形图 → 点击作答证据。**之后扩展到全量画像和推荐，不先做完整页面假数据。

## 9. 测试与验收

### 9.1 必需的测试矩阵

| 测试 | 关键断言 |
|---|---|
| 空历史 | 内部初值 0.5，对外未评估、mastery=null、曲线为空 |
| EMA 手算 | 正确、错误、正确得到 0.5840，计数与窗口一致 |
| 证据量边界 | 0、2、3、7、8 次分别得到预期分档 |
| 非有效记录 | needs_review 和确认状态下的 null 不影响 EMA，后者报告异常 |
| 稳定顺序 | 打乱输入后结果相同，同 answered_at 用 UUID 决定顺序 |
| 修订与撤销 | 修改当前结论后从头重放；修订不加次数；撤销后移除贡献 |
| 整题口径 | 多空题组只计一次，关联考点不产生画像贡献 |
| 历史快照 | 修改当前题库标签或题干后，既有画像归属和证据题干不变 |
| 重算幂等 | 连续两次重算汇总一致，Attempt 和 Revision 数量不变 |
| 清理旧汇总 | 某考点最后一条有效记录撤销后，不残留旧 mastery |
| 不同题目数 | 同题不同真实练习次数可增加，distinct 数只计一次 |
| 推荐约束 | 不超范围、不重复、不突破考点上限，不跨新旧配额补题 |
| 推荐冷启动 | 不根据内部 0.5 调整难度，采用教师基础难度与诊断理由 |
| 推荐时间 | 固定 as_of 可重复，边界天数符合约定，时间不直接改变 EMA |
| 候选不足 | 正确返回部分或空结果、缺口及限制原因 |
| 推荐解释 | 四项均在 0 至 1，贡献之和等于 score，证据 ID 属于该学生 |
| 接口边界 | UUID 错误、未知学生、跨学生证据访问、非法考点范围按约定响应 |
| 只读预览 | 查询画像与推荐前后 Assignment、PaperInstance、Attempt 数不变 |

纯函数测试不依赖数据库。持久化、快照与 API 集成测试继续使用 PostgreSQL，可用事务回滚或独立测试库构造边界记录；不要修改并提交现有 150 条演示历史。

M1 的 API 测试假定恰好 5 名学生、30 组题。因此冷启动学生只能在隔离测试中创建，不能永久加入当前演示库来满足测试。

M1 的 Attempt UUID 是入库时生成的，同一批次多条作答时间相同。相同数据库重放应稳定；跨全新数据库不要对混合正误序列写死每个学生的最终小数。手算精确值使用固定 UUID 的专用夹具验证。

### 9.2 自动验收脚本

新增 `backend/scripts/verify_m2.py`，默认只读，失败返回非零退出码。对未修改的 M1 种子基线建议检查：

```text
students: 5
valid_attempts: 150
materialized_mastery_rows: 50
assessed_exam_points_per_student: 10
sum_mastery_attempt_counts: 150
unmapped_snapshot_codes: 0
ownership_mismatches: 0
mastery_replay_mismatches: 0
validation_errors: 0
```

以上是待实现脚本的目标检查项，不是本次已运行的结果。按需重放值应与落库值量化后逐项一致；脚本还要验证计数关系、曲线末点、证据引用和未评估项，不只统计行数。

参数变化会影响推荐结果，因此推荐验收固定 `as_of`、范围和规则参数。不要要求当前 30 组题能在任意约束下满足任意题量，也不要求 5 人推荐列表两两不同。

### 9.3 完成实现后的运行命令

下列 `rebuild_mastery`、`verify_m2` 命令需要在相应脚本实现后才可执行。建议重算脚本支持 `--all`、`--student-code S001`、`--dry-run`，参数互斥关系由 argparse 校验。

```powershell
Set-Location 'D:\desktop\math-agent\code\backend'
$env:DATABASE_URL = 'postgresql+psycopg://postgres@localhost:55432/math_agent'

& '..\.conda-env\python.exe' -m scripts.rebuild_mastery --all --dry-run
& '..\.conda-env\python.exe' -m scripts.rebuild_mastery --all
& '..\.conda-env\python.exe' -m scripts.rebuild_mastery --all
& '..\.conda-env\python.exe' -m scripts.verify_m2
& '..\.conda-env\python.exe' -m scripts.verify_m1
& '..\.conda-env\python.exe' -m pytest -q
```

前端与 M0 回归：

```powershell
Set-Location 'D:\desktop\math-agent\code\frontend'
npm run build

Set-Location 'D:\desktop\math-agent\code'
& '.\.conda-env\python.exe' '.\scripts\validate_m0.py'
```

沿用现有表结构时无需新迁移。如果确需新增字段或约束，新增 0005，不能改写 0001 至 0004；同时注意现有 `verify_m1.py` 和健康接口测试将版本写死为 `0004`，应改为核对实际 Alembic head，不能简单放弃迁移版本验证。

### 9.4 手工演示路径

1. 打开 S001，查看概念、数轴等考点的实际表现，并点开支持作答。
2. 切换 S002，核对运算类关注项确由历史错误产生。
3. 查看 S003、S004，确认结果来自记录，不按学生编号硬编码能力。
4. 查看 S005 的运算考点曲线，解释存在的近期变化；若某考点证据不足，明确显示不足。
5. 展开全部 54 个考点，确认无证据项均未评估。
6. 设置明确的练习范围，查看推荐理由；故意收紧条件，验证候选不足状态。
7. 在隔离测试数据中演示零次、一次作答，不对学生作过度判断。

当前历史批次在 2026-09-01、09-08、09-15。以后演示时“距上次练习天数”会自然变化；测试使用固定 as_of，实际界面使用真实当前时间并展示基准时间。

在桌面及窄屏检查长考点名称、图表坐标、弹层、表格横向滚动和加载状态。条形图或曲线不能空白却无空态提示，切换学生不能短暂显示上一人的结论。

## 10. 交付清单与 M3 交接

M2 完成时应留下：

```text
backend/app/services/mastery.py
backend/app/services/student_profile.py
backend/app/services/recommendations.py
backend/app/schemas/profile.py
backend/app/schemas/recommendations.py
画像、曲线、证据和推荐相关 API
backend/scripts/rebuild_mastery.py
backend/scripts/verify_m2.py
相关纯函数与 PostgreSQL 集成测试
学生详情页的画像、证据和推荐预览
code/README.md 中的 M2 运行与验收说明
code/docs/06_M2_工作总结与M3交接.md
```

交接文档记录实际数据统计、通过的检查、规则版本、建议参数最终取值、推荐缺口示例及尚未完成事项。不能只写“画像已完成”。

交给 M3 的稳定接口是：

- 同一证据集可重放的学生画像服务。
- 教师范围、题量、难度等输入契约。
- 返回题目列表、理由、打分参数与缺口的推荐服务。
- 当前画像的证据 ID、修订号与计算时间。

M3 调用推荐服务后创建草稿试卷，并在创建 / 发布流程中保存选题原因、参数和题目快照；画像计算不进入试卷路由重复实现。M4 再复用同一重放函数完成改判闭环。

### 最终验收清单

- [ ] 画像来自当前有效 Attempt，使用历史主考点快照。
- [ ] 能从 0.5 稳定重放，汇总与曲线一致，曲线点可回溯修订号。
- [ ] 同一输入重算两次不重复计数，清除无有效证据的旧汇总。
- [ ] 未评估、证据较少和已有证据的状态区分明确。
- [ ] 同时显示有效次数、不同题目数、练习范围与最近错误。
- [ ] 推荐遵守教师范围、题型、难度、近期去重及考点上限。
- [ ] 推荐理由能追溯证据或教师参数，冷启动采用基础诊断策略。
- [ ] 候选不足返回明确缺口，不静默放宽规则。
- [ ] M0/M1 回归、M2 测试及前端构建通过，页面实际可操作。
- [ ] README 与 M2 工作总结、M3 交接文档已更新。

达到上述条件，即可进入 M3“个性化组卷、教师调整与发布”。
