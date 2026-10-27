# 数学教学 Agent Demo 技术设计方案

> 版本：v0.3（2026-09-23 修订）  
> 对应需求文档：`01_数学教学Agent_Demo需求分析.md`  
> 当前目标：完成本地可运行、可演示、可继续扩展的 Demo。  
> 数据权威来源：各章节“校本审核 DOCX”；manifest 作为导入辅助。  
> 当前限制：有理数、选择题 / 填空题、暂不拆子题。

交付节点与需求、开发规划保持一致：内部闭环原型为 M0–M4；教师演示版为 M0–M6，包含固定答题区图片识别。M 编号以开发步骤规划为准。需求以本次师生交流记录为准，`Ai(1).docx` 仅作背景参考。

---

# 1. 总体架构

第一版采用：

> **确定性业务工作流 + AI 增强模块**

而不是开放式自主 Agent。

```text
                   ┌─────────────────┐
                   │     React UI    │
                   └────────┬────────┘
                            │
                   ┌────────▼────────┐
                   │     FastAPI     │
                   │   Application   │
                   └────────┬────────┘
                            │
         ┌──────────────────┼──────────────────┐
         │                  │                  │
┌────────▼────────┐ ┌───────▼────────┐ ┌───────▼────────┐
│ PostgreSQL      │ │  Math / Rules  │ │ AI Adapter     │
│ 业务数据         │ │  SymPy + Rules │ │ VLM / LLM      │
└─────────────────┘ └────────────────┘ └────────────────┘
```

核心原则：

- 业务流程由系统控制；
- 题库、组卷、批改、画像等核心链路不依赖 LLM；
- AI 只处理适合 AI 的环节；
- AI 失败时，主业务仍可运行。

---

# 2. 推荐技术栈

## 2.1 前端

推荐：

```text
React
Ant Design
ECharts
```

用于：

- 教师后台
- 题库管理
- 学生画像
- 个性化组卷
- 作业批改
- 学情可视化

若仅为快速原型，可使用 Streamlit，但长期继续开发优先 React。

---

## 2.2 后端

推荐：

```text
FastAPI
```

优势：

- Python 数学与 AI 生态完善；
- REST API 开发快；
- 与 SymPy 集成方便。

---

## 2.3 数据库

推荐：

```text
PostgreSQL
```

第一版不使用 Neo4j。

知识结构：

```text
Chapter
→ Section
→ KnowledgePoint
→ ExamPoint
↔ Question
```

可以通过普通关系表和多对多关系实现；Demo 可先将章、节作为 KnowledgePoint 的结构化字段，保留题目与多个考点的关联。

---

## 2.4 数学答案判断

推荐：

```text
Python
+
SymPy
+
自定义答案归一化规则
```

注意：

- 不允许直接对学生输入执行 `eval`；
- 仅解析白名单数学字符和允许的函数；
- 限制表达式长度、解析复杂度和计算时间；
- 解析失败统一进入人工复核。

---

## 2.5 AI 接口

统一封装：

```text
VisionService
LLMService
```

业务代码不绑定模型厂商。

例如：

```python
VisionService.extract_answers(image)
LLMService.generate_hint(question_context)
```

未来可以替换模型供应商而不改变核心业务。

---

# 3. 业务模块

建议拆分：

```text
QuestionService
StudentService
RecommendationService
AssignmentService
GradingService
ProfileService
VisionService
AnalyticsService
```

最外层可设置：

```text
TeachingAgent
```

其职责是工作流编排，而不是自主 ReAct。

---

# 4. 核心试卷建模

这是第一版最重要的数据建模原则。

由于同一次作业中：

> **不同学生可能拿到不同题目**

因此不能把“所有题目”固定挂在一个公共 PaperVersion 下再让不同学生共享。

推荐结构：

```text
Assignment
└─ PaperInstance（某学生的一份实际试卷）
   └─ PaperInstanceItem（该学生试卷中的题目快照）
```

---

## 4.1 Assignment

表示：

> 一次逻辑上的作业任务。

例如：

```text
2026-09-23 有理数个性化练习
```

字段示意：

```text
id
title
chapter
teaching_scope
created_at
```

Assignment 本身不规定所有学生必须拿到相同题目。

---

## 4.2 PaperInstance

表示：

> 某个学生实际拿到的一份试卷。

字段建议：

```text
id
assignment_id
student_id

version_no
status
instance_code

supersedes_instance_id

created_at
published_at
```

状态：

```text
draft
published
```

规则：

- draft 状态可换题和调整；
- published 后不可修改；
- 如果发布后还要改，创建新的 PaperInstance；
- 新实例可通过 `supersedes_instance_id` 指向旧实例；
- `instance_code` 在发布时生成，全局唯一且不复用；同一作业、学生的版本号唯一；
- 正文、统一答题区和导出文件必须使用同一实例编号；
- 新版本不会改变旧编号的指向；旧版上传仍定位旧快照，不自动重定向到最新版本。

这样既支持个性化，又能保证历史可追溯。

---

## 4.3 PaperInstanceItem

表示某名学生某份实际试卷中的一道题。

字段建议：

```text
id
paper_instance_id
question_id
order_no

question_snapshot
options_snapshot
answer_snapshot
answer_rule_snapshot
exam_point_snapshot

answer_region
```

这里必须保存快照。`exam_point_snapshot` 包含主考点 ID 和关联考点；Demo 更新画像只使用主考点，关联考点用于检索和推荐，避免一道错题直接扣减所有相关考点。

`(paper_instance_id, order_no)` 唯一。多空题在答案结构中保存稳定的 `blank_id`，不能靠 OCR 返回顺序猜测对应关系。

原因：

> 后续即使题库内容被修改，也必须按照学生当时实际收到的题目和答案进行批改。

---

## 4.4 Attempt 的关联方式

Attempt 应优先关联：

```text
paper_instance_item_id
```

而不只是：

```text
question_id
```

原因：

- 同一道题未来可能修改；
- 学生做的是当时发布快照；
- 批改必须对应“实际试卷中的那一道题”。

Attempt 同时可以冗余保存 `question_id`，方便统计。

---

# 5. 其他核心数据模型

## 5.1 Question

建议字段：

```text
id
original_qid
chapter
section
question_type
difficulty

main_kp
main_exam_point

question_text
options
correct_answer

answer_match_type
answer_normalization_rule

solution
wrong_analysis

has_image
has_equation

source_file
created_at
updated_at
```

当前 `source_file` 指向对应校本审核 DOCX。

---

## 5.2 KnowledgePoint

```text
id
name
chapter
section
parent_id
```

---

## 5.3 ExamPoint

```text
id
name
knowledge_point_id
```

题目可挂：

- 一个主考点；
- 若干关联考点。

通过：

```text
QuestionExamPoint
```

实现多对多关系。

---

## 5.4 Student

```text
id
display_name
class_name
is_demo
created_at
```

Demo 阶段建议使用：

- 虚拟学生；
- 匿名编号；
- 非真实姓名。

---

## 5.5 StudentMastery

```text
student_id
exam_point_id

mastery
confidence_level

attempt_count
correct_count
recent_wrong_count

last_practiced_at
updated_at
```

---

## 5.6 Submission

```text
id
student_id
paper_instance_id
source
idempotency_key
attachments
submitted_at
```

Demo 中一份 PaperInstance 对应一个逻辑 Submission，`paper_instance_id` 唯一；Submission 的学生必须与试卷所有者一致。重复上传、补拍和手动修正复用同一 Submission。`attachments` 可先用 JSON 数组记录上传 ID、文件引用、内容哈希、时间和识别结果，后续按需拆表。

`idempotency_key` 标识首次提交请求，重试复用该键；相同键但请求内容不同应报冲突。新的上传请求仍受试卷唯一约束控制，不能创建第二份逻辑作答。文件哈希仅辅助发现完全相同图片，不能作为唯一去重依据。

`source`：

```text
manual
image
```

---

## 5.7 Attempt

每道题一次作答对应一条 Attempt；Demo 对 `paper_instance_item_id` 设置唯一约束。Attempt 是当前结论，历史修订由 AttemptRevision 保存。

字段：

```text
id
submission_id
student_id

paper_instance_item_id
question_id

student_answer
normalized_answer

grade_status
is_correct
ocr_confidence
recognition_status
manual_override
revision_no

answered_at
reviewed_at
```

`grade_status` 建议：

```text
auto_confirmed
needs_review
teacher_confirmed
```

只有：

```text
auto_confirmed
teacher_confirmed
```

的结果才允许更新学生画像。`needs_review` 时 `is_correct = null`，不能使用 false 代替未知。

`ocr_confidence` 只表示模型自报的识别分数，手动录入时为 null；不能表示判分正确概率。`recognition_status` 区分 `readable / blank / illegible / missing / ambiguous`，手动录入可为空。

## 5.8 AttemptRevision 与画像历史

AttemptRevision 以 `(attempt_id, revision_no)` 唯一，保存修订前后答案、自动判定、最终判定、状态、操作人、原因和时间。原始识别输出不可被教师修正覆盖，自动识别重跑也不得覆盖教师已确认的结果。

StudentMastery 是可重建的当前汇总。画像曲线从按作答时间排序的有效 Attempt 重放生成，每个点关联 Attempt 和其有效 revision_no；Demo 可按需计算，无需先增加专用时序数据库。

`answered_at` 首次登记时确定，缺少真实作答时间则采用首次提交时间并注明来源；补拍和改判不改变它。教师学情备注另存，不覆盖计算值。

---

# 6. 题库导入方案

## 6.1 权威来源

当前：

```text
校本审核 DOCX
```

作为题库内容权威来源。

manifest 如已提供则用于辅助索引；缺失时由 M0 整理生成，不作为导入前提。例如：

```text
qid
section
type
has_equation
has_image
preview
```

---

## 6.2 当前已知字段状态

不要把 manifest 的字段范围误认为题库全部字段。

按当前资料说明，校本审核 DOCX 预期可整理出以下字段；具体完整性和解析结果须在 M0 逐题核对：

```text
题目 ID
题型
难度
KP
考点
题干
参考答案
易错分析
```

因此第一版主要工作是：

> **把权威 DOCX 中已有信息结构化导入数据库，并校验程序解析结果。**

不是重新让教师从零补标整套题库。

---

## 6.3 导入流程

```text
DOCX + 可选 manifest
↓
解析题目和元数据
↓
转换为结构化 Question
↓
程序检查
↓
人工快速核对首批题目
↓
写入 PostgreSQL
```

重点检查：

- 公式是否丢失；
- 图片是否正确关联；
- 题型是否与题干一致；
- 答案是否正确抽取；
- 多空题答案结构是否完整。

---

## 6.4 首批题目

建议先整理：

```text
30 ~ 50 道
```

优先：

- 单一答案选择题；
- 单一答案填空题；
- 标准答案明确；
- 不依赖复杂图像；
- 可自动判分。

---

## 6.5 暂不拆子题

多空或多小问题仍保留为一个 Question。

例如：

```json
{
  "1": "A",
  "2": "-3",
  "3": "1/2"
}
```

但要注意：

- 每个空需要单独保存答案；
- 判分时可逐空判断；
- Demo 的 mastery 更新仍按整题主考点处理；
- 复杂多空题可直接进入人工复核。

因此首批数据尽量选择单一答案题，避免让“不拆子题”反过来增加 Demo 复杂度。

---

# 7. 答案判定设计

## 7.1 answer_match_type

第一版支持：

```text
choice
exact
numeric
```

---

## 7.2 choice

```text
normalize(" c ") → "C"
```

再进行直接比较。

---

## 7.3 exact

适用于必须严格符合格式的答案。

例如：

- 数学术语
- 指定单位
- 特定表达形式

---

## 7.4 numeric

使用安全解析和 SymPy 进行简单数学等价判断。

例如：

```text
1/2
0.5
2/4
```

数学上可以等价，但最终是否算正确仍受题目判分规则控制。

---

## 7.5 answer_normalization_rule

建议 JSON：

```json
{
  "trim_whitespace": true,
  "normalize_fullwidth_symbols": true,
  "allow_fraction_decimal_equivalence": true,
  "allow_equivalent_fraction": true,
  "require_simplified_fraction": false,
  "require_unit": false
}
```

如果题目要求最简分数：

```json
{
  "allow_equivalent_fraction": true,
  "require_simplified_fraction": true
}
```

则：

```text
1/2 → 正确
2/4 → 数学等价，但格式不符合要求
```

---

## 7.6 安全边界

学生答案进入数学解析前：

1. 限制输入长度；
2. 只接受允许字符；
3. 禁止 Python 代码和任意函数调用；
4. 限制 SymPy 解析 / 化简耗时；
5. 超时、异常或复杂度过高时返回 `needs_review`。

---

# 8. 个性化组卷算法

第一版不使用 LLM 推荐。

采用：

> 候选过滤 + 加权打分 + 约束抽样

---

## 8.1 候选过滤

根据：

```text
章节
题型
当前教学范围
难度
历史是否做过
题目是否可自动判分
```

产生候选集合。

---

## 8.2 推荐分

可先使用透明规则：

```text
score =
0.40 × weakness
+ 0.25 × recent_wrong
+ 0.20 × difficulty_match
+ 0.15 × forgetting
```

权重后续可调整，不应写死在业务概念中。各分项先归一化至 0–1，并保存选题原因与参数。距离上次练习时间仅是回顾优先级，不直接证明发生遗忘。

---

## 8.3 新知识

新知识保持当前教学进度一致；仅在相关考点证据足够时参考 mastery 调整难度。未练习或证据不足时使用教师设定的基础难度与少量诊断题，不能把默认 0.5 当作已测得的中等能力。

示例：

```text
mastery < 0.4
→ 优先低 / 中低难度

0.4 ~ 0.7
→ 优先中等难度

mastery > 0.7
→ 可加入较高难度
```

---

## 8.4 旧知识回顾

优先：

```text
mastery 较低
近期错题多
距离上次练习较久
最近 mastery 下降
```

---

## 8.5 去重和约束

避免：

- 同一试卷重复同一道题；
- 短时间内反复出现同一题；
- 某个考点占比过高；
- 超出教师指定教学范围。

教师分别配置新知识范围和已学回顾范围，后者不能扩展到尚未教学的知识。候选不足时返回缺失题量、考点和限制原因，由教师调整条件或接受较少题量；不得静默超范围、重复填充或调用模型补新题。不同画像应产生合理选题差异，但不要求每份试卷两两不同。

---

# 9. 学生画像冷启动

新学生不能因为第一道题答错就直接被判断为“明显薄弱”。

推荐：

```text
初始 mastery = 0.5
confidence_level = low
```

界面显示：

```text
数据不足，当前判断置信度较低
```

随着确认后的 Attempt 增多：

```text
low
→ medium
→ high
```

例如可暂定：

```text
0~2 次：low
3~7 次：medium
8 次以上：high
```

这些阈值仅是 Demo 的证据数量分档，可后续调整；`confidence_level` 在界面标为“作答证据量”，不能解释成统计置信概率。重复上传不计数，并同时展示不同题目数及练习范围，避免反复练少数题形成充分证据的假象。没有有效作答时显示“未评估”，0.5 只作为内部计算初值。

---

# 10. 学生 mastery 更新

第一版可使用简单 EMA：

```text
new_mastery =
(1 - α) × old_mastery
+
α × correctness
```

例如：

```text
α = 0.2
```

只有确认后的 Attempt 才参与更新。

即：

```text
auto_confirmed
teacher_confirmed
```

可更新；

```text
needs_review
```

不更新。

EMA 是可解释的选题参考指标，尚未校准题目难度和测量误差，不应当作学生真实能力百分比。

## 10.1 重复提交和改判后的重算

Demo 采用“按有效记录重算”，避免对同一 Attempt 重复累加：

1. 首次提交创建或取得该试卷唯一 Submission；每题取得唯一 Attempt。
2. 重复提交且内容未变化时返回已有结果，不新增有效作答或画像变化；有变化时进入修订流程。
3. 人工改判、识别修正、撤销确认时，追加 AttemptRevision 并更新当前结论；用预期 revision_no 检查并发冲突，防止旧页面覆盖新判断。
4. 取该学生受影响主考点的全部当前有效 Attempt，仅包含两个确认状态，按 `(answered_at, id)` 稳定排序，从 0.5 重放 EMA。
5. 同步重算作答数、正确数、近期错题、最后练习时间和画像曲线；撤销为待复核的记录不参与计算。
6. 修订、当前结论和画像汇总在同一事务中提交；按学生加锁串行处理，避免并发提交重复计数。失败时整体回滚。

历史改判在原作答时点修正曲线，不伪装成今天新学会一次；修订日志仍保留当时如何判定的历史。新一轮真实练习创建新的作业或试卷实例。

以后再考虑：

- 题目难度；
- 提示次数；
- 连续正确；
- 连续错误；
- 时间衰减；
- BKT / IRT / Elo。

---

# 11. 三层学生画像

逻辑上分为：

```text
能力层
↓
考点掌握层
↓
可行动弱点层
```

---

## 11.1 考点掌握层

核心层。

来自：

```text
Attempt
+
StudentMastery
```

---

## 11.2 能力层

Demo 暂定：

```text
计算能力
逻辑推理能力
概念理解能力
数形结合能力
```

上述维度为候选展示维度。只有明确“考点—能力”映射及作答证据后才可聚合；映射先由教师核对，展示时给出支持题目数和证据范围。

没有相关证据的维度（如当前题库不足以支持的空间想象能力）显示“暂未评估”，不能填成 0 或默认 50%。维度不足时优先用考点条形图和变化曲线，不强行绘制完整雷达图。聚合值仅用于教学辅助，不代表经过验证的能力测评。

---

## 11.3 可行动弱点层

根据历史 Attempt 产生。

例如：

```text
最近 5 道有理数乘除法题错 3 道
→ 有理数乘除法掌握不稳
```

若相关题目的易错分析反复涉及符号，可给出：

```text
建议重点复习负数运算符号规则
```

不能在无步骤证据时断言学生具体思维错误。

---

# 12. 学生试卷生成流程

```text
创建 Assignment
↓
为学生生成 PaperInstance(draft)
↓
生成 PaperInstanceItem
↓
教师换题 / 调整
↓
发布 PaperInstance
↓
冻结全部 PaperInstanceItem 快照
```

如果发布后需要修改：

```text
创建新的 PaperInstance
↓
version_no + 1
↓
supersedes_instance_id 指向旧实例
```

旧版本继续保留。正式导出只从 published 快照生成，草稿预览标明“未发布”；学生版不导出标答，教师答案页单独生成。

建议在系统内替题、发布后再导出。若在 Word 或纸上改题，须登记实际题目及新版本并核实对应编号；无法确定学生实际收到哪版时暂停自动批改，转人工核对。

---

# 13. 答案录入

内部闭环原型支持 Manual；教师演示版增加 Image，二者使用相同的 Submission、Attempt 和规则判分服务。

## 13.1 Manual

网页直接录入：

```text
1. B
2. -3
3. 1/2
...
```

这是内部闭环原型的正式输入方式，也是图片识别不可用时的兜底方案。

---

## 13.2 Image

教师上传答题卡图片。

若暂时没有自动二维码识别，可以先手动选择：

```text
学生
+
作业 / 试卷实例
```

再上传，并核对纸面编号。编号缺失或与所选实例不一致时暂停，先确认版本和归属；不能根据学生姓名直接取最新实例。

---

# 14. instance_code 与二维码

内部闭环原型必须实现：

```text
instance_code
```

发布 PaperInstance 时生成唯一短码。

教师演示版导出“试卷正文 + 页底统一答题区”，必须把：

```text
instance_code
```

打印在正文和答题区；可以同时增加对应 QR Code。固定答题区按最终题号排布，首批使用单答案题；支持多空题时另带 blank_id。

二维码只包含非个人信息标识，例如：

```text
instance_code
```

服务器再查询：

```text
PaperInstance
→ Student
→ PaperInstanceItem
→ 答案快照
```

二维码属于低成本增强项，但：

> **不作为第一版 OCR 功能完成的阻塞条件。**

---

# 15. VLM 设计

VLM 只负责：

> 提取学生最终填写的答案。

推荐返回：

```json
{
  "answers": [
    {
      "order_no": 1,
      "answer": "B",
      "recognition_status": "readable",
      "confidence": 0.98
    },
    {
      "order_no": 2,
      "answer": "-3",
      "recognition_status": "readable",
      "confidence": 0.92
    },
    {
      "order_no": 3,
      "answer": null,
      "recognition_status": "illegible",
      "confidence": 0.35
    }
  ]
}
```

不确定时：

```text
answer = null
grade_status = needs_review
```

`confidence` 存入 Attempt.ocr_confidence。请求只提供答题图、预期题号及空序号，不提供标准答案，避免识别时按标答补全。后端核验返回结构、题号范围、重复题号及缺失项；缺失项标记 missing，不能自动填成空白或错误。blank 表示看到空白，illegible 表示看不清，ambiguous 表示有多个可能答案。

模型自报 confidence 未经校准，不是正确概率。教师演示版初期，所有图片识别答案先进入 needs_review，由教师对照原图确认或批量确认清晰项目，再按题目规则判分；空白也需确认后按未作答处理。后续只有经代表性样本核验的识别策略才开放自动确认，不能只按一个高分阈值放行。

识别样本保存人工真值，分别记录逐题答案提取准确率、漏识别率、需修改比例和每份作业复核耗时；格式统一后比较提取文本，不能用数学等价掩盖抄读错误。先报告实测结果，不承诺未经验证的准确率。

---

# 16. 答题区域与图片处理

当答题卡布局固定后，可以保存：

```text
answer_region
```

例如：

```json
{
  "x1": 0.10,
  "y1": 0.20,
  "x2": 0.45,
  "y2": 0.27
}
```

未来流程：

```text
上传整页
↓
矫正倾斜 / 透视
↓
裁剪姓名等非必要区域
↓
根据 answer_region 裁题
↓
VLM / OCR 识别
```

教师演示版可以先由教师上传匿名答题区照片或手动裁剪；自动裁剪、透视校正、独立答题卡和批量多页识别后置。answer_region 坐标只有在图像完成模板对齐后才能直接用于裁剪，不把模板坐标直接套到任意照片上。

---

# 17. 隐私与外部模型调用

Demo 阶段：

- 使用虚拟学生或匿名编号；
- 不向外部模型发送真实姓名、学号；
- 优先只上传答题区域；
- 若整页上传，应尽量先裁掉姓名等身份区域；
- 图片是否落盘、保存多久、何时删除应可配置；
- 外部模型调用日志中避免记录不必要的个人信息。

正式学校试用前，再单独设计：

- 数据存储位置；
- 权限管理；
- 云端 / 本地部署；
- 境外 API 使用政策；
- 数据保留周期。

---

# 18. 人工复核机制

以下情况：

```text
VLM confidence 低
答案无法解析
数学等价判断失败
多个空无法匹配
图片模糊
```

进入：

```text
grade_status = needs_review
```

教师可：

```text
修正学生答案
手动判对
手动判错
```

教师确认后：

```text
grade_status = teacher_confirmed
manual_override = true
```

只有最终确认结果才能更新画像。重新识别不得冲掉教师结论；教师改判或撤销确认按第 10.1 节重算，保留修订历史。

---

# 19. 提示与错题反馈

优先复用题库已有：

```text
wrong_analysis
solution
exam_point
```

展示：

```text
本题考点
学生答案
标准答案
常见易错点
简短提示
```

如果调用 LLM 生成提示，输入应基于题库已有内容，不要求模型重新解题。

---

# 20. API 设计建议

## 20.1 题库

```text
GET  /questions
GET  /questions/{id}
POST /questions/import
PUT  /questions/{id}
```

---

## 20.2 学生

```text
GET /students
GET /students/{id}
GET /students/{id}/profile
```

---

## 20.3 作业与个性化试卷

```text
POST /assignments
POST /assignments/{id}/generate-instances

GET  /paper-instances/{id}
GET  /paper-instances/{id}/print
GET  /paper-instances/by-code/{instance_code}
POST /paper-instances/{id}/replace-item
POST /paper-instances/{id}/publish
POST /paper-instances/{id}/revise
```

---

## 20.4 提交与批改

```text
POST /submissions/manual
POST /submissions/image
POST /submissions/{id}/grade

PUT /attempts/{id}/review
```

提交接口携带明确 paper_instance_id 和幂等键；同一试卷的追加图片返回既有 Submission。review 携带 expected_revision_no、修正内容及原因；并发版本不符时返回冲突。同一已确认结果的重复操作直接返回已有结果，不重复产生画像变化。

---

## 20.5 学情

```text
GET /students/{id}/mastery
GET /students/{id}/attempts
GET /analytics/class
```

---

# 21. 前端页面

第一版建议：

```text
1. 题库
2. 学生列表
3. 学生画像
4. 个性化组卷
5. 作业批改
6. 学情总览
```

---

# 22. 页面关键交互

## 22.1 学生画像

展示：

```text
考点掌握度
能力雷达图
最近作业
最近错题
薄弱点
掌握度曲线
作答证据量（confidence_level）
```

未评估维度不显示为低分；每个曲线点可查看对应作答与改判记录。

---

## 22.2 个性化组卷

流程：

```text
创建 Assignment
↓
选择学生 / 班级
↓
选择章节和当前教学范围
↓
生成各学生 PaperInstance
↓
查看推荐原因
↓
换题
↓
逐个或批量发布
```

---

## 22.3 批改页面

每题展示：

```text
题号
学生答案
标准答案
自动判断
原始答案图与识别状态
OCR 自报分数（不代表正确概率）
是否需人工复核
常见易错点
```

---

# 23. Demo 开发顺序

## 第一阶段：题库与基础数据

完成：

```text
PostgreSQL schema
有理数题库导入
30~50 道首批题快速核对
题库查看页面
虚拟学生
```

---

## 第二阶段：学生画像

完成：

```text
StudentMastery
初始 mastery = 0.5
confidence
画像页面
```

---

## 第三阶段：个性化试卷

完成：

```text
Assignment
PaperInstance
PaperInstanceItem
规则推荐
换题
发布冻结
```

---

## 第四阶段：手动批改闭环

先不依赖 OCR。

完成：

```text
手动答案录入
答案归一化
自动判分
Attempt
人工复核状态
mastery 更新
再次组卷
```

到这里内部闭环原型（M0–M4）成立，尚未完成教师演示版。

---

## 第五阶段：可视化与演示打磨（M5）

完善考点图表、证据说明、班级学情、推荐原因、演示数据及关键业务测试；验证重复提交和历史改判后的画像一致性。

---

## 第六阶段：固定答题区与 VLM（M6）

从发布快照导出带实例编号的试卷与统一答题区，接入真实图片上传、VisionService、原图核对与教师确认，使用同一判分和画像链路。完成识别样本核验后达到教师演示版交付节点。二维码自动识别可后置。

---

# 24. Demo 稳定性策略

必须确保：

```text
AI 服务失败
≠
整个 Demo 失败
```

因此：

- 组卷不依赖 LLM；
- 选择 / 填空判分优先使用规则；
- 图片识别失败可以手动输入；
- 画像更新不依赖模型；
- AI 结果均可人工修正。

---

# 25. 本地部署建议

第一版可使用：

```text
Docker Compose
```

服务：

```text
frontend
backend
postgres
```

AI 接口由后端调用外部 API。

目录示例：

```text
project/
├── frontend/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── services/
│   │   └── core/
│   └── tests/
├── data/
├── scripts/
├── docker-compose.yml
└── README.md
```

---

# 26. 第一阶段暂不实现

暂缓：

```text
Neo4j
复杂 RAG
多 Agent
ReAct
BKT / IRT
复杂手写步骤识别
证明题自动判分
自动生成正式新题
完整学校权限体系
```

---

# 27. 量化验收标准

## 27.1 内部闭环原型（M0–M4）

至少满足：

```text
1. ≥30 道已核对的有理数选择 / 填空题可导入、检索、组卷。

2. ≥5 名虚拟学生有可追溯的不同作答历史，选题差异符合画像与教学范围。

3. 教师替换某学生题目并发布后，批改严格使用该学生的
   PaperInstanceItem 快照，而不是当前题库内容。

4. 同一作业重复提交时，判分一致，Attempt 数及画像均不重复增加。

5. needs_review 的答案在教师确认前不更新 StudentMastery。

6. 每一次 mastery 变化都能追溯到具体 Attempt 和
   paper_instance_item_id。

7. 教师改判或撤销确认后，画像与当前有效记录重算结果一致。

8. 完成一次作答后再次组卷，薄弱考点 / 错题相关题目权重发生变化。
```

---

## 27.2 教师演示版（M0–M6）

- 满足内部闭环原型验收及 M5 演示打磨要求。
- 从发布快照导出学生试卷与答题区，编号、题序、空序号一致，学生版无标答。
- 新旧版本同时存在时，旧版上传仍对应旧版答案；编号异常先人工确认。
- 使用真实照片完成提取、原图核对、教师确认、规则判分和画像更新。
- 样本覆盖负号、分数、空白、涂改和模糊情况，按第 15 节报告识别质量及复核耗时；模拟 VLM 输出只能用于开发测试。
- API 超时、漏题、重复题号、补拍和重复上传均不会误更新画像；可用手动录入继续业务。

---

# 28. 关键技术原则

1. **校本审核 DOCX 是当前权威题库来源。**
2. **manifest 是索引，不是完整题库。**
3. **一次 Assignment 下，每个学生有独立 PaperInstance。**
4. **发布后的 PaperInstance 不可变。**
5. **PaperInstanceItem 保存题目和答案规则快照。**
6. **Attempt 关联 PaperInstanceItem，而不只关联 Question。**
7. **只有确认后的 Attempt 才更新画像。**
8. **新学生 mastery 从中性值开始，并展示 confidence。**
9. **填空题按题配置答案判定规则。**
10. **数学解析采用白名单与资源限制。**
11. **AI 是增强模块，不是单点依赖。**
12. **instance_code 在内部闭环原型实现；教师演示版完成固定答题区识别，二维码自动识别可后置。**
13. **Demo 优先使用虚拟 / 匿名学生，减少真实教育数据外发。**
14. **先跑通闭环，再增加复杂智能能力。**
15. **重复上传不重复计数，改判按有效作答历史重算画像。**
16. **OCR 分数、判分状态和画像证据量分别表示，不混用为“AI 置信度”。**

---

# 29. 当前最小可运行闭环

```text
导入 30~50 道有理数题
↓
创建 ≥5 名虚拟学生
↓
初始化 mastery = 0.5 + 不同模拟历史
↓
创建 Assignment
↓
为每名学生生成独立 PaperInstance
↓
教师换题
↓
发布并冻结
↓
手动录入学生答案
↓
规则判分 / 人工复核
↓
生成 Attempt
↓
更新 mastery
↓
展示画像变化
↓
重新生成下一份个性化练习
```

该闭环跑通后，再接答题卡 VLM 识别。
