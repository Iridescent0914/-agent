# 数学教学 Agent Demo 第五步开发指导：M4 手动批改、人工复核与画像闭环

> 编写日期：2026-09-27  
> 实施收尾更新：2026-09-29（评分时序、并发/回滚测试与公式键盘）  
> 前置阶段：M0、M1、M2、M3 已完成  
> 对应总规划：`03_数学教学Agent_Demo开发步骤规划.md`  
> 直接输入：`code/docs/07_M3_工作总结与M4交接.md`  
> 本阶段目标：在不依赖 OCR、VLM 或 LLM 的情况下，完成“录入答案 → 确定性判分 → 教师复核 → 画像重算 → 下一轮选题变化”的内部闭环。

---

## 1. M4 要交付什么

M4 不是单独增加一个“批改页面”，而是把 M1 已建的作答实体、M2 的可重放画像、M3 的发布快照真正连接起来。

完成后的主链路应为：

```text
定位一份当前有效的已发布学生试卷
→ 教师按题号录入完整答案
→ 首次提交读取题库当时已发布且可机判的答案，并冻结本次实际评分依据
→ choice / exact / numeric / group 确定性判分
→ 无法可靠判定的题进入 needs_review
→ 教师确认、改判或撤销确认
→ 追加 AttemptRevision，不覆盖历史
→ 在同一事务中按当前有效作答重算该学生画像
→ 再次组卷时，错题和薄弱考点改变推荐权重
```

### 1.1 本阶段必须完成

- 支持通过 `paper_instance_id` 进入录入页，也支持先用 `instance_code` 精确定位；
- 只允许对当前有效的 `published` 试卷创建新 Submission；
- 一份 `PaperInstance` 只有一个 Submission，一道 `PaperInstanceItem` 只有一个 Attempt；
- 支持 `choice`、`exact`、`numeric`、`latex_exact` 和现有题库中的 `group` 答案结构；
- 保存原始答案、归一化答案、自动判定详情、最终判定和时间信息；
- 无法安全解析或规则不完整时进入 `needs_review`，不得伪装成答错；
- 教师可以确认、改判、修正录入答案，或把已确认结果撤回为待复核；
- 每次有效变化追加一条 `AttemptRevision`，无变化的重复请求不追加修订；
- 使用 `expected_revision_no` 防止旧页面覆盖新结果；
- 提交、修订、审计记录和画像重算处于同一数据库事务；
- 重复点击、网络重试和并发请求不会重复增加 Attempt 或画像计数；
- 前端提供录入、批改结果、待复核队列和修订记录查看；
- 增加 M4 单元测试、集成测试、数据库约束测试和 `verify_m4.py`。

### 1.2 阶段边界

M4 暂不实现：

- 图片上传、OCR、VLM 答案识别；
- 二维码识别、答题区裁剪、透视校正；
- 证明题、解答题和复杂书写过程自动评分；
- 按步骤给分、分值统计、总分和等级；
- 多次真实练习复用同一试卷；
- 多教师账号、角色权限和真实审计身份；
- 撤回或被新版替代的试卷创建新的 Submission；
- 用 LLM 猜测无法解析的答案。

其中，图片识别留到 M6。M4 的手动录入本身是正式业务路径，不是临时模拟 OCR。

### 1.3 M4 完成的判断标准

以下五件事必须同时成立：

1. 判分结果可由固定输入和固定规则重复得到；
2. 任一画像变化能追溯到 `Attempt.id + revision_no + PaperInstanceItem`；
3. 重复请求不会制造第二次学习证据；
4. 历史改判后，画像等于从全部当前有效作答重新播放的结果；
5. 新一轮推荐能明确引用本次作答带来的近期错误或掌握度变化。

---

## 2. 当前基础与开工检查

### 2.1 已有成果

当前工程已经具备 M4 所需的主要实体和上游能力：

- Alembic 当前为 `0010 head`；
- `Submission`、`Attempt`、`AttemptRevision` 已在 M1 建表；
- `Submission.paper_instance_id` 唯一；
- `Attempt.paper_instance_item_id` 唯一；
- `AttemptRevision(attempt_id, revision_no)` 唯一；
- M3 已实现发布冻结、唯一实例码、撤回与版本替代；
- `PaperInstanceItem` 已保存题干、答案、判分规则和主考点快照；
- M2 已实现按 `(answered_at, Attempt.id)` 稳定重放；
- `needs_review` 已被 M2 排除在画像证据之外；
- `persist_mastery(db, context)` 已支持幂等写入、更新和删除陈旧汇总；
- M3 请求级数据库依赖已经统一在成功后 commit、异常时 rollback。

M4 不应重新设计画像公式，也不应回写或绕开 M3 快照。

### 2.2 优先阅读的现有文件

开工前按下面顺序阅读：

```text
03_数学教学Agent_Demo开发步骤规划.md
02_数学教学Agent_Demo技术设计方案.md
code/docs/07_M3_工作总结与M4交接.md
code/docs/10_试卷生命周期与基础数据管理补充完善.md

code/backend/app/models/entities.py
code/backend/app/services/mastery.py
code/backend/app/services/student_profile.py
code/backend/app/services/paper_workflow.py
code/backend/app/services/paper_snapshots.py
code/backend/app/core/database.py

code/backend/tests/test_mastery.py
code/backend/tests/test_profile_api.py
code/backend/tests/test_m3_workflow.py
code/backend/tests/test_m3_db_guards.py

code/frontend/src/pages/PaperInstancePage.tsx
code/frontend/src/pages/AssignmentDetailPage.tsx
code/frontend/src/api/client.ts
code/frontend/src/types/assignment.ts
```

### 2.3 开工基线

先确认环境和旧阶段回归正常，不要重建环境或重新导入种子：

```powershell
cd "D:\desktop\math-agent\code"
docker compose up -d
docker compose ps

docker compose exec -T backend python -m alembic current
docker compose exec -T backend python -m pytest -q
docker compose exec -T backend python -m scripts.verify_m1
docker compose exec -T backend python -m scripts.verify_m2
docker compose exec -T backend python -m scripts.verify_m3
```

如果使用当前电脑的项目内环境：

```powershell
cd "D:\desktop\math-agent\code\backend"
$env:DATABASE_URL = "postgresql+psycopg://postgres@localhost:55432/math_agent"
& "..\.conda-env\python.exe" -m alembic current
& "..\.conda-env\python.exe" -m pytest -q
& "..\.conda-env\python.exe" -m scripts.verify_m3
```

基线应至少满足：

```text
Alembic: 0010
backend: 52 passed 或更多
verify_m1 / verify_m2 / verify_m3: validation_errors = 0
frontend build: passed
```

若数量因后续正常新增测试发生变化，以“全部通过、旧验收无错误”为准。

---

## 3. 先固定 M4 的十条业务不变量

### 3.1 发布快照不可变，评分依据必须在提交时冻结

试卷发布快照始终保留：

```text
PaperInstanceItem.answer_snapshot
PaperInstanceItem.answer_rule_snapshot
PaperInstanceItem.exam_point_snapshot
```

首次录入尚未创建 Submission 时，应读取题库中当时已发布且可机判的 `Question.correct_answer` 和判分规则：若题库答案在录入前已经修订，首次评分直接使用修订后的答案，并把实际评分依据冻结到 Attempt 判分详情中；考点归属仍使用 `PaperInstanceItem.exam_point_snapshot`。

Submission 创建后，普通查询和教师修正必须使用该 Attempt 已冻结的评分依据，不得因题库后续编辑而静默改变历史结果。若题库答案在提交后再次修订并发布，只提示教师执行“重新评分”；重新评分追加修订、替换 Attempt 的当前评分依据并重算画像，但不得改写 `PaperInstanceItem` 发布快照。

### 3.2 创建提交前必须锁定准确版本

新建 Submission 前必须确认：

```text
PaperInstance.status == "published"
PaperInstance.invalidated_at is null
PaperInstance.student_id == request.student_id
```

旧实例码不能静默跳转最新版。撤回或已被更新的试卷仍可查看历史，但不能创建新提交。

如果一份试卷在有效时已经提交，之后才被撤回或替代，已有 Submission 和 Attempt 仍保留，并允许教师继续完成复核；禁止的只是“失效后新建提交”。

### 3.3 一份试卷只产生一组学习证据

实体基数固定为：

```text
1 PaperInstance : 0..1 Submission
1 PaperInstanceItem : 0..1 Attempt
1 Attempt : 1..N AttemptRevision
```

修改答案或改判只能更新同一个 Attempt 的当前状态并追加修订，不能创建第二个 Attempt。

学生真正再次练习同一道题时，必须通过新 Assignment / 新 PaperInstance 产生新的学习证据。

### 3.4 `answered_at` 表示原作答时间

首次提交时为每道题固定 `answered_at`。之后教师改判、修正录入或撤销确认，都不得把它改成审核时间。

```text
answered_at  原作答时间，参与画像排序
reviewed_at  最近一次教师审核时间
revision.created_at  本次修订发生时间
```

否则历史改判会被错误地当成当天新练习。

### 3.5 `needs_review` 不是答错

状态语义固定为：

| grade_status | is_correct | 是否进入画像 | 含义 |
| --- | --- | --- | --- |
| auto_confirmed | true / false | 是 | 规则引擎可靠完成判分 |
| needs_review | null | 否 | 无法可靠判定，等待教师 |
| teacher_confirmed | true / false | 是 | 教师确认或改判后的最终结果 |

不允许 `needs_review + false`，也不允许确认状态下 `is_correct = null`。

### 3.6 自动判定与教师最终判定都要保留

教师改判后不能只留下最后的布尔值。至少需要保留：

- 自动判分引擎版本；
- 自动状态与自动结果；
- 归一化后的学生答案；
- 使用的匹配类型和关键规则；
- 进入人工复核的原因码；
- 教师最终状态与结果；
- 每次修订的前后完整状态、原因和操作人。

### 3.7 修订是追加式审计，不是覆盖日志

`Attempt` 保存当前状态，`AttemptRevision` 保存每次状态变化后的完整快照。

```text
首次创建 Attempt：revision_no = 1，before_state = null
第一次有效修改：revision_no = 2
第二次有效修改：revision_no = 3
```

`AttemptRevision` 创建后不可更新或删除。

### 3.8 旧修订号永远不能覆盖新状态

所有教师复核请求都必须携带 `expected_revision_no`。服务先锁行，再比较：

```text
expected_revision_no == Attempt.revision_no  才允许继续
expected_revision_no != Attempt.revision_no  返回 409
```

即使陈旧请求希望写入的值恰好与当前值相同，也返回 409，让前端刷新后重新判断。

### 3.9 画像更新必须与作答事务原子一致

以下操作必须在同一事务中完成：

```text
写 Submission
→ 写或更新 Attempt
→ 追加 AttemptRevision
→ flush
→ load_context(student_id)
→ persist_mastery(...)
→ commit
```

任何一步失败都整体 rollback。不能先返回批改成功，再异步尝试更新画像。

### 3.10 同一学生的写操作必须串行

提交和改判前对 `Student` 行执行 `SELECT ... FOR UPDATE`。这样即使两个不同试卷同时修改同一学生，也不会同时基于旧证据重算并互相覆盖画像。

锁顺序固定为：

```text
Student
→ PaperInstance / Submission
→ Attempt（按 id 或 order_no 稳定排序）
```

所有 M4 写服务使用同一顺序，避免死锁。

---

## 4. 答案数据合同

### 4.1 单答案题

单答案题的请求中统一使用 JSON 标量：

```json
{
  "paper_instance_item_id": "uuid",
  "order_no": 1,
  "response_status": "answered",
  "answer": "B"
}
```

明确留白使用：

```json
{
  "paper_instance_item_id": "uuid",
  "order_no": 2,
  "response_status": "blank",
  "answer": null
}
```

不要用“请求中缺少该题”代表留白。首次手动提交必须覆盖试卷全部题目，题目集合不完整、重复或顺序不匹配时返回 422。

手动录入来源下 `recognition_status` 和 `ocr_confidence` 保持 `null`；不要伪造 OCR 字段。

### 4.2 小题组

现有题库包含 `answer_match_type = group`。学生答案使用稳定的 `item_id`，不依赖数组位置：

```json
{
  "paper_instance_item_id": "uuid",
  "order_no": 3,
  "response_status": "answered",
  "answer": {
    "kind": "group",
    "items": [
      {"item_id": "1", "answer": "C"},
      {"item_id": "2", "answer": "B"},
      {"item_id": "3", "answer": null}
    ]
  }
}
```

服务端必须：

- 校验 `kind == group`；
- 拒绝重复或未知 `item_id`；
- 要求答案项集合与 `answer_snapshot.items` 一致；
- 按标准答案中的 `item_id` 顺序生成规范化结果；
- 使用各标准答案项自带的 `answer_match_type`；
- 把每小题结果写入 `grading_details.item_results`。

当前 `Attempt.is_correct` 仍为整组布尔值：只有所有小题均确定且正确时为 `true`；存在确定错误时整题为 `false`；如果没有确定错误但至少一项无法判定，则整题进入 `needs_review`。

`partial_credit` 在 M4 只用于展示“答对 x/y 小题”，不折算分数，也不按小题分别更新画像。这样与现有“一道 `PaperInstanceItem` 对应一个 Attempt、一个主考点”的模型保持一致。

### 4.3 请求中的题号与 ID

前端展示题号，但后端以 `paper_instance_item_id` 为身份键。`order_no` 作为额外交叉校验：

```text
ID 属于该试卷且 order_no 一致  接受
ID 属于该试卷但 order_no 不一致  422
ID 属于其他试卷                  422
只有 order_no、没有 item_id       不接受写入
```

这可避免调序后旧页面把答案写到错误题目。

### 4.4 规范化结果和判分详情

建议统一的 `grading_details` 结构：

```json
{
  "engine_version": "m4-grading-v1",
  "match_type": "numeric",
  "auto_status": "auto_confirmed",
  "auto_is_correct": true,
  "reason_code": "equivalent_value",
  "rule": {
    "allow_fraction_decimal_equivalence": true,
    "allow_equivalent_fraction": true,
    "require_simplified_fraction": false
  },
  "item_results": [],
  "warnings": []
}
```

不要把标准答案复制进普通学生视图；教师批改详情可以读取原快照展示。

---

## 5. 判分内核设计

### 5.1 模块拆分

建议新增：

```text
code/backend/app/services/grading/
├── __init__.py
├── contracts.py       # GradeResult、ItemGradeResult、错误码
├── normalize.py       # Unicode、空白、选项等纯归一化
├── numeric.py         # 有界数值表达式解析
└── engine.py          # choice / exact / numeric / group 分派
```

核心函数保持纯函数，不访问数据库：

```python
def grade_answer(
    student_answer: object,
    answer_snapshot: object,
    answer_rule_snapshot: dict,
    *,
    response_status: str,
) -> GradeResult:
    ...
```

`GradeResult` 至少包含：

```text
normalized_answer
grade_status
is_correct
reason_code
details
```

这样可以单独做大量参数化单元测试，也方便 M6 把识别结果接到同一个判分入口。

### 5.2 通用归一化

只执行规则明确允许的变换：

- 限制单个答案长度，建议最多 128 个字符；
- Unicode NFKC 或显式全角数字、字母、括号和运算符转换；
- `trim_whitespace=true` 时去除首尾空白；
- 不默认删除所有内部空格；
- 不默认替换中文词义；
- 不修改保存的 `student_answer`，仅产生独立 `normalized_answer`。

原始输入过长、类型不符或结构错误属于请求校验错误；合法结构但无法可靠判定才是 `needs_review`。

### 5.3 choice

建议规则：

```text
全角转半角
→ 去首尾空白
→ uppercase（规则允许时）
→ 必须是单个 A-Z 字母
→ 与 answer_snapshot 比较
```

边界：

- `b` 在 `uppercase=true` 时可归一化为 `B`；
- `Ｂ` 在全角归一化开启时可归一化为 `B`；
- `选B`、`B.`、选项正文不能擅自当成 `B`，除非以后显式增加规则；
- 明确留白是可靠的错误，结果为 `auto_confirmed + false`；
- 非法格式进入 `needs_review`，不自动算错。

### 5.4 exact

`exact` 表示按配置完成有限归一化后严格比较：

- 默认保留大小写和内部空白；
- 只应用 `answer_normalization_rule` 中明确开启的规则；
- 规范化完成且不相等时，为可靠错误；
- 不使用模糊匹配、编辑距离、同义词或 LLM；
- 对公式文本也不要暗中升级为代数等价判断。

例如 `a+b=b+a` 与 `b+a=a+b` 在 `exact` 下不是同一个字符串。若老师希望代数等价，应在后续新增明确的匹配类型和审核规则，而不是改变历史 `exact` 语义。

### 5.5 numeric

M4 的 numeric 目标是安全支持有理数和简单算术表达式，不是通用计算器。

建议第一版只允许：

```text
整数、小数
一元正负号
+ - * / 和括号
可选的有限整数幂
```

安全边界：

- 原始长度最多 128；
- 只允许数字、空白、点号、括号和白名单运算符；
- 禁止变量名、属性访问、函数调用、下标、字符串和关键字；
- 禁止 `eval`、`exec` 和直接处理不受限输入的 `sympy.parse_expr`；
- AST 节点数、嵌套深度和指数绝对值必须有上限；
- 分母为 0、超限、语法错误或计算超时进入 `needs_review`；
- 使用 `Fraction` 或等价精确有理数表示，避免浮点误差。

推荐先用 `ast.parse(..., mode="eval")`，然后只递归解释允许的数值常量、`UnaryOp` 和指定 `BinOp`；绝不调用 `eval`。如果后续确需 SymPy 处理更复杂的代数等价，只能在更严格的语法白名单后进入有时间和资源上限的隔离进程，并把超时返回为 `needs_review`。

规则必须来自快照：

```text
allow_fraction_decimal_equivalence
allow_equivalent_fraction
require_simplified_fraction
require_unit
```

不要为了通过示例而忽略规则。例如：

| 标准答案 | 学生答案 | 规则 | 期望 |
| --- | --- | --- | --- |
| 1/2 | 0.5 | fraction_decimal=true | 正确 |
| 1/2 | 2/4 | equivalent_fraction=true | 正确 |
| 1/2 | 2/4 | require_simplified=true | 错误 |
| 1/2 | 0.5 | fraction_decimal=false | 按规则判错 |
| 1/2 | `__import__(...)` | 任意 | needs_review，不执行 |

当前种子中不少 numeric 规则把等价开关设为 `false`，应尊重发布时的实际快照。M4 验收应另外构造开关为 `true` 的测试题，验证 `1/2`、`0.5`、`2/4` 的规则差异，而不是全局强行视为相等。

### 5.6 group

`group` 由 `engine.py` 递归调用单项判分器：

```text
读取 answer_snapshot.items
→ 按 item_id 对齐学生答案
→ 对每项调用 choice / exact / numeric
→ 汇总 item_results
→ 生成整题状态
```

汇总规则：

```text
全部项目可判且正确                    auto_confirmed / true
至少一项可判且错误                    auto_confirmed / false
没有确定错误，但至少一项无法判定       needs_review / null
```

如果业务之后要求“某项无法判定但另一项已错仍需人工检查”，可以调整为更保守策略；M4 v1 必须固定一种规则并写进 `engine_version`，不要在页面层临时决定。

### 5.7 统一原因码

建议至少定义：

```text
matched
mismatched
blank_answer
invalid_choice_format
invalid_answer_type
invalid_group_shape
missing_group_item
unknown_group_item
unsupported_match_type
invalid_rule_snapshot
numeric_parse_failed
numeric_division_by_zero
numeric_complexity_exceeded
numeric_timeout
equivalent_value
format_rule_failed
```

API 返回稳定原因码，前端再映射中文说明。不要让前端解析后端异常字符串。

---

## 6. 数据库增量设计

### 6.1 新增 `0011`，不要改写旧迁移

建议新增：

```text
code/backend/alembic/versions/0011_add_m4_grading_guards.py
```

现有表已经提供大部分字段，不需要重建模型。建议最小增量如下。

#### Submission

新增：

```text
request_fingerprint  varchar(64), nullable
```

用途：保存首次手动提交请求的规范化 SHA-256，用于判断同一个幂等键或同一试卷的重复请求是否真的同载荷。

历史种子 Submission 可保持 `null`；M4 新建的手动 Submission 必须由服务写入非空 fingerprint。

#### Attempt

新增：

```text
grading_details  jsonb, not null, default {}
```

其中保存当前答案对应的自动判分详情。教师最终结果继续使用已有 `grade_status` 和 `is_correct`；教师修改前后的自动与最终状态同时进入 `AttemptRevision.after_state`。

不要把 `AttemptRevision` 简化为只记 `is_correct`。完整状态建议包含：

```json
{
  "student_answer": "0.5",
  "normalized_answer": {"kind": "rational", "numerator": 1, "denominator": 2},
  "grading_details": {"engine_version": "m4-grading-v1"},
  "grade_status": "teacher_confirmed",
  "is_correct": true,
  "recognition_status": null,
  "ocr_confidence": null,
  "manual_override": true,
  "answered_at": "2026-09-27T10:00:00+08:00",
  "reviewed_at": "2026-09-27T10:05:00+08:00"
}
```

### 6.2 补强状态约束

在旧约束基础上增加或替换为等价的完整约束：

```sql
(grade_status = 'needs_review' AND is_correct IS NULL)
OR
(grade_status IN ('auto_confirmed', 'teacher_confirmed') AND is_correct IS NOT NULL)
```

并建议约束：

```text
manual_override = true 只允许 teacher_confirmed 或 needs_review
ocr_confidence 为 null 或位于 0..1
revision_no > 0
```

### 6.3 补强跨表归属约束

普通 `CHECK` 不能查询其他表。建议在 PostgreSQL 中使用约束触发器或等价数据库守卫，事务结束前检查：

```text
Submission.student_id == PaperInstance.student_id
Attempt.submission_id 对应的 paper_instance_id
    == PaperInstanceItem.paper_instance_id
Attempt.student_id == Submission.student_id
Attempt.question_id == PaperInstanceItem.question_id
Attempt.revision_no 存在对应 AttemptRevision
```

约束触发器使用 `DEFERRABLE INITIALLY DEFERRED`，允许服务在同一事务中先写 Attempt 再写初始 Revision。

### 6.4 审计表只追加

增加数据库触发器拒绝：

```text
UPDATE attempt_revisions
DELETE attempt_revisions
```

如果需要修正审计记录，只能追加新修订。迁移和测试清理应通过事务回滚完成，不在正式数据上物理改日志。

### 6.5 迁移顺序

```text
1. 新增 nullable request_fingerprint
2. 新增 grading_details，server_default='{}'
3. 回填或确认历史行满足新状态约束
4. 替换状态 CHECK
5. 创建跨表约束触发器和审计只追加触发器
6. 更新 SQLAlchemy 模型
7. 测试从空库 upgrade head
8. 测试现有 0010 数据库升级到 0011
```

不要修改 `0003_create_workflow_tables.py`；它已经是历史迁移。

---

## 7. 提交、复核与画像重算服务

### 7.1 推荐模块

```text
code/backend/app/services/submission_workflow.py
code/backend/app/services/attempt_review.py
code/backend/app/schemas/submissions.py
code/backend/app/api/routes/submissions.py
```

职责边界：

| 模块 | 职责 |
| --- | --- |
| grading | 纯函数归一化和判分，不访问数据库 |
| submission_workflow | 校验试卷、幂等创建 Submission/Attempt、初始修订、画像重算 |
| attempt_review | 乐观锁、确认/改判/撤销、追加修订、画像重算 |
| student_profile | 继续负责加载证据和持久化汇总，不感知 HTTP |
| route | 请求解析、错误映射和响应，不复制业务规则 |

### 7.2 首次手动提交算法

建议严格按以下顺序实现：

```text
1. 开启请求事务
2. 查询 PaperInstance 和学生身份
3. SELECT Student ... FOR UPDATE
4. SELECT PaperInstance ... FOR UPDATE
5. 验证 published、当前有效、学生一致
6. 读取并按 order_no 排序全部 PaperInstanceItem
7. 查询并锁定对应 Question，解析“录入当时已发布且可机判”的评分依据
8. 校验请求答案集合与试卷题目集合完全一致
9. 生成 canonical payload 和 request_fingerprint
10. 查询并锁定已有 Submission
11. 若为重复请求，执行幂等分支
12. 创建 Submission(source='manual', attachments=[])
13. 使用第 7 步解析出的依据对每道题调用 grade_answer
14. 创建 Attempt(revision_no=1)，并冻结实际评分答案、规则和题库 revision
15. 为每个 Attempt 创建 AttemptRevision(revision_no=1)
16. flush，使当前修订和关联全部可查询
17. load_context(db, student_id)
18. persist_mastery(db, context)
19. 由统一请求依赖 commit
20. 返回 Submission 详情和画像更新摘要
```

所有题的 `answered_at` 建议使用同一个带时区时间。教师可以选择实际作答时间时，也只允许合理范围，并统一转换为带时区值。

### 7.3 幂等分支

规范化 fingerprint 至少覆盖：

```text
paper_instance_id
student_id
按 paper_instance_item_id 稳定排序的 response_status 和原始答案
```

不把客户端发送时间、字段顺序或无意义空白 JSON 格式计入 hash。

处理规则：

| 情况 | 行为 |
| --- | --- |
| 相同 idempotency_key、相同 fingerprint | 返回原结果，`replayed=true`，不写库 |
| 相同 idempotency_key、不同 fingerprint | 409 `idempotency_payload_conflict` |
| 同一 paper 已有 Submission、fingerprint 相同 | 返回原结果，不写库 |
| 同一 paper 已有 Submission、fingerprint 不同 | 409，提示使用教师修订接口 |
| key 已被另一 paper 使用 | 409 |

不能捕获唯一约束异常后简单返回 500。并发情况下数据库唯一约束仍是最后防线；服务应将可识别冲突映射成 409，并重新读取已存在结果。

### 7.4 初始修订

每个新 Attempt 都必须同时创建 revision 1：

```text
before_state = null
after_state = 当前完整状态
operator_type = "system"
reason = "manual_submission_auto_grade"
```

如果自动判分进入 `needs_review`，仍然需要 revision 1；它只是暂不进入画像。

### 7.5 教师复核算法

教师接口支持三种动作：

```text
confirm    确认或改判为 teacher_confirmed + true/false
correct    修正录入答案，重新自动判分后由教师给出最终结论
reopen     撤销确认，变为 needs_review + null
```

推荐处理顺序：

```text
1. SELECT Student ... FOR UPDATE
2. SELECT Attempt ... FOR UPDATE，并加载 Submission、PaperItem、Paper
3. 比较 expected_revision_no；不一致立即 409
4. 校验 action、最终结果和 reason
5. 如果修改 student_answer，重新执行快照判分并更新 normalized_answer/grading_details
6. 构造目标当前状态
7. 若目标状态与当前状态完全一致，返回 unchanged，不新增修订
8. 保存 before_state
9. Attempt.revision_no += 1
10. 更新当前字段，answered_at 保持不变
11. 新增 AttemptRevision，operator_type='teacher'
12. flush
13. load_context + persist_mastery
14. commit
```

`reason` 必填并限制长度，例如 2～500 字。Demo 操作人统一为 `demo_teacher`，但服务参数应保留 actor，方便以后接真实账号。

### 7.6 状态转换

允许的主要转换：

| 当前状态 | 操作 | 目标状态 |
| --- | --- | --- |
| needs_review | confirm | teacher_confirmed |
| auto_confirmed | confirm / override | teacher_confirmed |
| teacher_confirmed | override | teacher_confirmed，新 revision |
| auto_confirmed | reopen | needs_review |
| teacher_confirmed | reopen | needs_review |
| needs_review | correct + confirm | teacher_confirmed |

教师操作不得把状态写成 `auto_confirmed`。自动状态只由判分引擎在首次提交时产生。

### 7.7 画像重算策略

M4 首版每次有效写入后重算该学生全部考点，不做局部增量：

```python
context = load_context(db, student_id)
stats = persist_mastery(db, context)
```

理由：

- 历史改判会影响后续 EMA 的每个点；
- 撤销确认可能删除某考点的最后一条有效证据；
- 当前 Demo 每名学生数据量小，全量重放成本可控；
- 复用 M2 已验证逻辑，减少两套算法产生漂移。

不要只对当前考点做 `mastery += ...`，也不要把旧 mastery 作为不可重建的真值。

### 7.8 已有教师确认不能被重跑覆盖

M4 暂无 OCR 重跑，但服务应提前固定规则：

- 已存在 Submission 时，重新提交不能覆盖 Attempt；
- `teacher_confirmed` 只能通过带 `expected_revision_no` 的教师接口改变；
- 后续 M6 识别重跑只追加识别附件或生成待比较结果，不能直接覆盖教师状态；
- 改判必须产生新 revision。

---

## 8. API 契约建议

### 8.1 路由

建议新增：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/v1/grading/queue` | 查询未提交、待复核和已完成试卷 |
| GET | `/api/v1/paper-instances/{id}/submission` | 获取录入所需试卷及已有批改结果 |
| POST | `/api/v1/paper-instances/{id}/submissions/manual` | 首次完整手动提交 |
| GET | `/api/v1/submissions/{id}` | 获取提交、题目和修订摘要 |
| PATCH | `/api/v1/attempts/{id}/review` | 确认、改判、修正答案或撤销确认 |
| GET | `/api/v1/attempts/{id}/revisions` | 获取完整修订历史 |

按实例码定位继续复用 M3：

```text
GET /api/v1/paper-instances/by-code/{instance_code}
```

解析后必须使用返回的真实 `paper_instance_id`，不能自行猜测最新版。

### 8.2 首次提交请求示例

```json
{
  "idempotency_key": "manual-RAT-ABCD-EFGH-20260927-001",
  "student_id": "uuid",
  "answered_at": "2026-09-27T09:30:00+08:00",
  "answers": [
    {
      "paper_instance_item_id": "uuid-1",
      "order_no": 1,
      "response_status": "answered",
      "answer": "B"
    },
    {
      "paper_instance_item_id": "uuid-2",
      "order_no": 2,
      "response_status": "blank",
      "answer": null
    }
  ]
}
```

`answered_at` 可选；缺省时由服务器生成。不能信任客户端传入的 student 与试卷关系，服务仍需从数据库核验。

### 8.3 教师复核请求示例

确认或改判：

```json
{
  "action": "confirm",
  "expected_revision_no": 1,
  "is_correct": true,
  "corrected_answer": null,
  "reason": "核对原卷后确认该答案等价",
  "actor": "demo_teacher"
}
```

修正录入并确认：

```json
{
  "action": "correct",
  "expected_revision_no": 2,
  "is_correct": false,
  "corrected_answer": "-3",
  "reason": "首次手动录入把负号漏掉",
  "actor": "demo_teacher"
}
```

撤销确认：

```json
{
  "action": "reopen",
  "expected_revision_no": 3,
  "is_correct": null,
  "corrected_answer": null,
  "reason": "需再次核对纸质原卷",
  "actor": "demo_teacher"
}
```

### 8.4 响应中的关键字段

提交详情至少返回：

```text
submission_id
paper_instance_id
instance_code
student
submitted_at
source
replayed
summary.total_count
summary.correct_count
summary.incorrect_count
summary.needs_review_count
attempts[]
mastery_update_stats
```

每个 Attempt 至少返回：

```text
attempt_id
paper_instance_item_id
order_no
question_snapshot
student_answer
normalized_answer
grade_status
is_correct
grading_details
revision_no
answered_at
reviewed_at
manual_override
```

### 8.5 HTTP 错误约定

| 状态码 | 场景 |
| --- | --- |
| 404 | 试卷、Submission 或 Attempt 不存在 |
| 409 | 幂等键载荷冲突、已有不同提交、修订号陈旧、并发唯一键冲突 |
| 422 | 试卷未发布或已失效、学生不匹配、答案集合不完整、答案结构非法、动作字段矛盾 |
| 500 | 非预期错误，事务必须回滚 |

继续复用项目现有结构化错误格式，并增加稳定 `code`：

```text
paper_not_gradable
student_paper_mismatch
answer_set_mismatch
idempotency_payload_conflict
submission_already_exists
stale_attempt_revision
invalid_review_transition
```

---

## 9. 前端开发

### 9.1 页面与路由

建议新增：

```text
/grading                                  批改工作台
/paper-instances/:paperInstanceId/grading 单份试卷录入与复核
/submissions/:submissionId                提交详情，可与上一页合并
```

新增主要文件：

```text
code/frontend/src/pages/GradingQueuePage.tsx
code/frontend/src/pages/PaperGradingPage.tsx
code/frontend/src/components/ManualAnswerEditor.tsx
code/frontend/src/components/AttemptReviewPanel.tsx
code/frontend/src/components/RevisionTimeline.tsx
code/frontend/src/types/submission.ts
```

在 `AppShell` 增加“作业批改”导航；在 Assignment 详情和已发布 Paper 页面增加“录入/查看批改”入口。

### 9.2 批改工作台

列表至少展示：

```text
作业标题
学生编号与姓名
实例码
版本号与当前有效状态
提交状态：未录入 / 待复核 / 已完成
待复核题数
提交时间
```

默认只展示当前有效发布版本，但应允许筛选已有 Submission 的历史失效版本，便于继续复核历史提交。

### 9.3 手动录入页

录入页必须从发布快照渲染：

- 展示实例码、学生和版本状态；
- 按 `order_no` 展示题干、选项和输入框；
- 选择题使用明确的 A/B/C/D 选择器；
- 单空填空使用文本输入；
- 小题组按 `item_id` 展示多个输入；
- 非选择题支持普通输入与 MathLive 可视化公式输入切换；
- 常用公式工具栏至少提供分数、平方根、n 次根、平方、幂、下标、绝对值、括号、π、正负号、乘除号、比较符号和度数符号；
- “打开完整公式键盘”提供数字、符号、字母和希腊字母布局；点击模板后应选中第一个占位符，便于连续填写；
- `numeric` 公式输入按 ASCII Math 提交，`latex_exact` 按 LaTeX 提交；只保存文本表达式，不保存公式截图；
- 提供“学生留空”状态，不用空字符串猜测；
- 首次提交前检查每道题都已选择“作答”或“留空”；
- 提交按钮防重复点击，并由前端生成稳定 idempotency key；
- 网络失败重试必须复用同一个 key；
- 提交成功后切换为结果/复核视图，不再次 POST。

### 9.4 结果与复核

每题清晰显示：

```text
原始学生答案
归一化结果
标准答案（仅教师视图）
自动判定及原因
当前最终判定
当前 revision_no
是否进入画像
```

`needs_review` 使用醒目但不等同“错误”的视觉状态。教师复核时：

- 必须填写原因；
- 请求携带当前 `revision_no`；
- 409 时弹出“结果已被其他操作更新”，重新加载；
- 成功后刷新当前 Attempt、汇总和学生画像摘要；
- 无变化请求显示“当前结果未变化”，不伪造新修订。

### 9.5 修订时间线

时间线按 `revision_no` 显示：

```text
修订号
操作类型（system / teacher）
原因
状态变化
答案变化
判定变化
发生时间
```

不要只展示最后一次改判；M4 的可信度很大程度来自可追溯审计。

### 9.6 完成闭环的页面反馈

提交或改判成功后，页面可以展示：

```text
本次有效作答数变化
待复核数
受影响主考点
画像持久化 inserted / updated / deleted / unchanged
```

同时提供“查看学生画像”和“基于最新画像再次组卷”入口。不要在前端自行计算 mastery。

---

## 10. 建议实施顺序

### 批次 A：固定合同和数据库守卫

1. 新增 `schemas/submissions.py` 的请求响应模型；
2. 固定单题、小题组、留白和 review action 的 JSON 结构；
3. 新增 `0011` 迁移和模型字段；
4. 增加状态约束、归属触发器和 revision 只追加保护；
5. 完成迁移升级、空库升级和降级可行性检查。

完成标志：数据库可以表达自动结果、教师结果和完整审计，错误归属数据会被拒绝。

### 批次 B：纯判分内核

1. 实现通用归一化；
2. 实现 choice；
3. 实现 exact；
4. 实现安全 numeric；
5. 实现 group 汇总；
6. 完成原因码与参数化单元测试。

完成标志：判分服务不依赖数据库即可稳定通过全部边界用例。

### 批次 C：首次提交事务

1. 实现试卷与学生校验；
2. 实现答案集合校验和 fingerprint；
3. 实现学生行锁和幂等分支；
4. 创建 Submission、Attempt 和 revision 1；
5. 接入 `load_context + persist_mastery`；
6. 完成事务回滚、重复请求和并发测试。

完成标志：同一份试卷无论重复点击多少次，数据库都只有一个 Submission 和每题一个 Attempt。

### 批次 D：教师复核与历史改判

1. 实现 `expected_revision_no`；
2. 实现 confirm / correct / reopen；
3. 实现无变化 no-op；
4. 追加完整 AttemptRevision；
5. 每次有效变化后重算画像；
6. 验证历史改判不改变 `answered_at`。

完成标志：改错为对、改对为错、撤销为待复核后，画像均与独立全量重放一致。

### 批次 E：API 和前端闭环

1. 注册 submissions 路由；
2. 增加 grading queue；
3. 完成手动录入页；
4. 完成结果、复核和 revision 时间线；
5. 从作业与试卷页接入入口；
6. 完成桌面端和 390px 移动端测试。

### 批次 F：自动验收和交接

1. 新增 `verify_m4.py`；
2. 新增完整集成场景；
3. 运行 M0～M4 全部回归；
4. 更新 `code/README.md`；
5. 编写 `code/docs/11_M4_工作总结与M5交接.md`。

---

## 11. 测试矩阵

### 11.1 判分单元测试

#### choice

- `B` 与 `B`；
- `b` 在 uppercase 开关打开和关闭时；
- 全角 `Ｂ`；
- `B.`、`选B`、多个字母；
- 明确留白；
- 超长答案。

#### exact

- 首尾空格开关；
- 全角符号开关；
- 内部空格不被意外删除；
- 大小写不被意外改变；
- 看似代数等价但字符串不同；
- 中文字符答案。

#### numeric

- 正负整数和小数；
- `1/2`、`0.5`、`2/4` 在不同规则组合下；
- `1-(2+3)`；
- 除以 0；
- 过深括号、过大指数、超长输入；
- 字母、函数调用、属性访问和恶意表达式；
- 解析失败和超时均进入 `needs_review`；
- 不产生浮点精度误判。

#### group

- 全部正确；
- 部分错误；
- 某项无法解析；
- 缺少 item；
- 重复 item；
- 未知 item；
- item 顺序不同但 ID 正确；
- 混合 choice / exact / numeric。

### 11.2 提交集成测试

必须覆盖：

1. 对有效 published 试卷首次提交成功；
2. draft 试卷拒绝提交；
3. withdrawn / superseded 试卷拒绝新提交；
4. 学生与试卷不匹配时拒绝；
5. 题目缺失、重复、跨试卷 item 拒绝；
6. 判分只使用快照，修改当前 Question 后结果不变；
7. 相同 key 和载荷重复提交为 no-op；
8. 相同 key 不同载荷返回 409；
9. 相同 paper 新 key 相同载荷为 no-op；
10. 相同 paper 不同载荷提示走 review；
11. 初始每个 Attempt 都存在 revision 1；
12. `needs_review` 不进入 StudentMastery；
13. 事务中途异常时 Submission、Attempt、Revision 和 Mastery 全部回滚。

### 11.3 复核与重算测试

必须覆盖：

1. needs_review 确认为正确后进入画像；
2. auto_confirmed 错误改判为正确；
3. teacher_confirmed 正确改判为错误；
4. 已确认结果 reopen 后从画像排除；
5. 修改原始答案后重新保留自动判分详情；
6. 每次有效变化 revision_no 只增加 1；
7. 无变化请求不增加 revision；
8. 陈旧 expected_revision_no 返回 409；
9. 改判不改变 answered_at；
10. persist 后再次独立全量重放，所有汇总字段完全一致；
11. 撤销某考点最后一条证据后，对应 StudentMastery 行被删除；
12. 人工确认不能被重复提交覆盖。

### 11.4 并发测试

使用两个独立数据库 Session，而不是在单 Session 中模拟：

- 同一 paper 两次并发首次提交，最终只有一个 Submission；
- 同一 Attempt 两个相同 expected revision 的并发改判，只能一个成功；
- 同一学生不同试卷并发提交，学生行锁使画像最终等于全量重放；
- 锁等待结束后不出现重复 Attempt、跳号 revision 或丢失画像更新。

测试使用事务隔离数据或唯一测试前缀，不向正式演示库留下作业。

### 11.5 数据库守卫测试

直接绕过服务写入非法数据，确认数据库拒绝：

- Submission 学生与 Paper 学生不一致；
- Attempt 指向其他 Paper 的 item；
- Attempt.question_id 与 item.question_id 不一致；
- confirmed 状态但 `is_correct=null`；
- needs_review 但 `is_correct=false`；
- 更新或删除 AttemptRevision；
- 当前 revision_no 没有对应 revision 行；
- 同一 paper 第二个 Submission；
- 同一 item 第二个 Attempt。

### 11.6 前端测试

Playwright 至少覆盖：

1. 从已发布试卷进入录入页；
2. 选择题、填空题、小题组和留白录入；
3. 双击提交不会重复创建；
4. 自动结果和待复核状态正确显示；
5. 教师确认后 revision 更新；
6. 模拟 409 后提示刷新而非静默覆盖；
7. 修订时间线可展开；
8. 390px 下能完成录入和复核；
9. 已失效且未提交试卷不能开始录入；
10. 已有提交的历史失效试卷仍能查看和继续复核。
11. 点击“插入分数”会生成真正的 `\frac{...}{...}` 结构，而不是普通斜杠文本；
12. 常用公式工具栏在桌面与 390px 页面均可换行显示，不产生横向溢出。

---

## 12. `verify_m4.py` 设计

新增：

```text
code/backend/scripts/verify_m4.py
```

默认模式只读检查现有数据：

```text
Alembic 是否为 0011 head
Submission 与 Paper / Student 归属是否一致
Attempt 与 Submission / Item / Question 归属是否一致
每个 Attempt 当前 revision 是否存在
revision 是否从 1 连续递增
状态与 is_correct 是否一致
needs_review 是否未进入画像证据
每名有 M4 数据的学生，StudentMastery 是否等于重放结果
同一 paper / item 是否存在重复记录
是否有 M4 新提交缺失 request_fingerprint
```

建议另提供事务回滚演练参数：

```powershell
python -m scripts.verify_m4 --exercise
```

`--exercise` 在测试事务中：

```text
选取或创建临时 published paper
→ 首次提交
→ 重复提交
→ 改判
→ reopen
→ 检查画像
→ rollback
```

正式输出示例：

```json
{
  "submission_count": 1,
  "attempt_count": 10,
  "revision_count": 13,
  "pending_review_count": 1,
  "mastery_students_checked": 1,
  "idempotency_errors": 0,
  "revision_errors": 0,
  "mastery_mismatches": 0,
  "validation_errors": 0
}
```

即使正式库尚无教师创建的 M4 Submission，也应明确输出 `submission_count = 0` 和 warning；结构与迁移检查仍须执行。M4 最终验收前至少通过 UI 完成一份真实演示提交。

---

## 13. 完成实现后的命令

### 13.1 Docker 路径

```powershell
cd "D:\desktop\math-agent\code"

docker compose up -d
docker compose exec -T backend python -m alembic current
docker compose exec -T backend python -m pytest -q
docker compose exec -T backend python -m scripts.verify_m1
docker compose exec -T backend python -m scripts.verify_m2
docker compose exec -T backend python -m scripts.verify_m3
docker compose exec -T backend python -m scripts.verify_m4
docker compose run --rm frontend npm run build
docker compose run --rm frontend npm run test:e2e
```

### 13.2 项目内本地环境

```powershell
cd "D:\desktop\math-agent\code\backend"
$env:DATABASE_URL = "postgresql+psycopg://postgres@localhost:55432/math_agent"

& "..\.conda-env\python.exe" -m alembic upgrade head
& "..\.conda-env\python.exe" -m pytest -q
& "..\.conda-env\python.exe" -m scripts.verify_m1
& "..\.conda-env\python.exe" -m scripts.verify_m2
& "..\.conda-env\python.exe" -m scripts.verify_m3
& "..\.conda-env\python.exe" -m scripts.verify_m4 --exercise

cd "..\frontend"
npm run build
npm run test:e2e
```

### 13.3 迁移检查

至少验证两条路径：

```text
空数据库：0001 → 0011
现有数据库：0010 → 0011
```

升级现有正式演示库前先备份；备份文件放在 `.runtime/`，不要提交到 Git。

---

## 14. 手工演示与验收路径

建议固定使用一个有明确薄弱点的虚拟学生，例如 S002，并保留演示前画像截图或关键数值。

### 14.1 标准闭环

1. 打开学生画像，记录当前薄弱考点和掌握度；
2. 创建新 Assignment，为该学生生成草稿；
3. 查看推荐理由，确认包含现有薄弱点；
4. 发布试卷并记录 `instance_code`；
5. 从“作业批改”进入该试卷；
6. 录入包含正确、错误和一个需复核格式的答案；
7. 提交后查看自动判分和 `needs_review`；
8. 确认待复核项；
9. 打开学生画像，确认作答次数、近期错误和曲线变化；
10. 对一条历史结果执行改判，确认 revision 时间线和画像回算；
11. 撤销另一条确认，确认它从画像证据中移除；
12. 再次组卷，查看本轮错题或薄弱考点进入推荐依据。

### 14.2 必演的可靠性场景

- 连续点击两次提交，Attempt 数量不变；
- 刷新页面重新提交同一请求，返回已有结果；
- 打开两个页面复核同一题，第二个陈旧请求收到 409；
- 修改当前 Question 标准答案后，历史试卷仍按发布快照判分；
- 把早期一次“错”改成“对”，画像曲线在原作答时间位置变化；
- 把已确认结果撤销为待复核，画像有效作答数减少；
- 再次组卷时推荐依据发生可解释变化。

---

## 15. M4 完成后应提交的产物

### 15.1 后端

```text
app/services/grading/*
app/services/submission_workflow.py
app/services/attempt_review.py
app/schemas/submissions.py
app/api/routes/submissions.py
alembic/versions/0011_add_m4_grading_guards.py
scripts/verify_m4.py
tests/test_grading.py
tests/test_m4_submission_workflow.py
tests/test_m4_review_workflow.py
tests/test_m4_concurrency.py
tests/test_m4_db_guards.py
```

文件可按实现适度合并，但职责不能混乱。

### 15.2 前端

```text
批改工作台
手动答案录入
自动判分结果
待复核与教师确认/改判
撤销确认
修订时间线
画像与再次组卷入口
桌面与移动端 E2E
```

### 15.3 文档

更新：

```text
code/README.md
code/docs/11_M4_工作总结与M5交接.md
```

工作总结至少记录：

- 最终 API；
- 最终 migration 版本；
- 判分规则版本和安全边界；
- 幂等与锁策略；
- revision 状态机；
- 测试数量和验收结果；
- M5 可直接使用的页面和数据；
- M6 接入识别结果时必须遵守的合同。

### 15.4 给 M5 的稳定输入

M5 可以直接使用：

- 批改工作台和提交汇总；
- 每题最终状态与待复核统计；
- 学生画像、掌握度曲线和近期错误；
- AttemptRevision 审计时间线；
- 完整的端到端演示数据；
- 可重复的 M4 自动测试和验收脚本。

### 15.5 给 M6 的稳定输入

M6 只需把图片识别结果转换成与 M4 相同的答案合同，再复用判分引擎。M6 不得改变：

```text
PaperInstance 精确版本绑定
PaperInstanceItem 快照判分
Submission / Attempt 唯一性
AttemptRevision 追加式审计
expected_revision_no
teacher_confirmed 不被识别重跑覆盖
画像事务重算
```

图片附件、识别状态和置信度可以在 M6 增量扩展，但不能另造一套批改链路。

---

## 16. 常见返工点

### 16.1 回读当前题库答案

错误：每次查看或修正历史提交时，都临时按最新 `Question.correct_answer` 重新判断。  
后果：老师修改题库后，历史批改结果静默漂移。  
正确：首次录入使用当时题库中已发布且可机判的答案并冻结评分依据；提交后的题库变更只产生“重新评分”提示，必须由教师显式触发并追加修订。原 `PaperInstanceItem` 发布快照始终不变。

### 16.2 把解析失败算成错误

错误：numeric 解析异常时设置 `false`。  
后果：技术失败污染画像。  
正确：`needs_review + null`。

### 16.3 每次提交都创建新 Attempt

错误：把网络重试当成再次练习。  
后果：作答次数、EMA 和近期错误全部重复。  
正确：一 item 一 Attempt，变化只追加 revision。

### 16.4 只增量修改 mastery

错误：改判时直接给当前 mastery 加减一个值。  
后果：无法恢复历史顺序，曲线和汇总不一致。  
正确：按当前有效证据全量重放该学生。

### 16.5 改判时更新 answered_at

错误：把审核时间当作练习时间。  
后果：错误进入“最近作答”，影响推荐。  
正确：answered_at 永久保留，另写 reviewed_at 和 revision.created_at。

### 16.6 只靠前端防重复和并发

错误：按钮 disable 就认为不会重复。  
后果：刷新、重试或并发请求仍可能写重。  
正确：前端防抖 + 幂等键 + 行锁 + 唯一约束 + 乐观锁。

### 16.7 直接使用 `eval` 或不受限 SymPy 解析

错误：把学生输入直接交给通用表达式执行器。  
后果：安全、资源耗尽和不可预测行为风险。  
正确：有限语法、节点/深度/指数限制、精确有理数计算，失败进复核。

### 16.8 小题组拆成多个 Attempt

错误：一组题的每小题都新建 Attempt。  
后果：与当前 PaperInstanceItem 和画像口径不一致，作答次数膨胀。  
正确：一组题一个 Attempt，细项结果放 `grading_details.item_results`。

### 16.9 教师确认被自动重跑覆盖

错误：重复提交或未来 OCR 重跑直接更新当前 Attempt。  
后果：人工最终结论丢失。  
正确：teacher_confirmed 只能由带修订号的教师操作改变。

### 16.10 无变化也追加 revision

错误：重复点击“确认”制造审计噪声。  
正确：先比较完整目标状态，相同则 no-op；陈旧修订号仍必须 409。

---

## 17. M4 最终验收清单

### 数据与约束

- [ ] Alembic 从 `0010` 升级到 `0011` 成功；
- [ ] 空数据库可以从头升级到 head；
- [ ] Submission、Attempt、Item、Student 归属由服务和数据库双重校验；
- [ ] `AttemptRevision` 只追加；
- [ ] confirmed / needs_review 与 `is_correct` 组合合法；
- [ ] 每个 Attempt 都存在当前 revision。

### 判分

- [ ] choice 归一化和比较稳定；
- [ ] exact 只执行显式规则；
- [ ] numeric 无 `eval`，并有长度、语法、复杂度和耗时边界；
- [ ] `1/2`、`0.5`、`2/4` 按各题快照规则正确处理；
- [ ] group 按 item_id 对齐并保留逐项结果；
- [ ] 无法判断时进入 needs_review，不污染画像。

### 幂等与事务

- [ ] 一份 Paper 只有一个 Submission；
- [ ] 一道 PaperItem 只有一个 Attempt；
- [ ] 相同请求重复提交不新增记录；
- [ ] 相同幂等键不同载荷返回 409；
- [ ] 并发提交不重复计数；
- [ ] 任一步失败时作答、修订和画像整体回滚；
- [ ] 同一学生的画像写入被串行化。

### 人工复核

- [ ] confirm / correct / reopen 均可用；
- [ ] expected_revision_no 生效；
- [ ] 旧页面不能覆盖新结果；
- [ ] 有效变化追加完整 revision；
- [ ] 无变化请求不追加 revision；
- [ ] answered_at 在改判后保持不变；
- [ ] 自动判定和教师最终判定都可追溯。

### 画像闭环

- [ ] needs_review 不进入画像；
- [ ] 确认后画像更新；
- [ ] 改判后画像等于全量重放；
- [ ] reopen 后证据从画像移除；
- [ ] 每个曲线点可定位 Attempt、revision 和 PaperItem；
- [ ] 再次组卷时本轮错题和薄弱考点影响推荐。

### 页面与质量

- [ ] 批改工作台可用；
- [ ] 单题、小题组和留白均可录入；
- [ ] 分数、根式、幂、下标等常用公式可通过可视化按钮录入，完整公式键盘可用；
- [ ] 待复核状态不显示成普通答错；
- [ ] 修订时间线可查看；
- [ ] 409 冲突有明确刷新提示；
- [ ] 桌面与 390px 页面可完成全流程；
- [ ] M0～M4 后端回归、前端 build 和 E2E 全部通过；
- [ ] `verify_m4.py` 输出 `validation_errors = 0`。

---

## 18. 现在最先做的一件事

先不要写页面。第一步应建立判分合同和测试夹具：

1. 从现有已发布快照中各挑一个 `choice`、`exact`、`numeric` 和 `group` 样本；
2. 为每个样本列出原始答案、规范化结果、状态、布尔结果和原因码；
3. 增加专门的等价开关测试题，明确 `1/2`、`0.5`、`2/4` 的不同结果；
4. 固定 `GradeResult` 和提交 JSON；
5. 先完成纯函数测试，再新增 `0011` 和事务服务。

只要判分输出合同、状态语义和修订状态机先稳定，后续 API、数据库事务和前端页面都能围绕同一事实来源实现；如果先从页面开始，最容易在小题组、解析失败、重复提交和改判重算处返工。
