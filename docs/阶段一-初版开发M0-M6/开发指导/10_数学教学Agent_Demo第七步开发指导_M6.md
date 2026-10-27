# 数学教学 Agent Demo 第七步开发指导：M6 固定答题区与 VLM

> 对应《03_数学教学Agent_Demo开发步骤规划.md》的 M6。  
> 开工基础：M0～M5 的题库、画像、组卷、发布、手动批改、审计和班级学情已经实现。  
> 阶段目标：从发布快照导出可打印试卷与统一答题区，完成真实图片上传、答案提取、教师核对、规则判分、画像更新和下一轮组卷，达到教师演示版交付节点。  
> 核心原则：VLM 只抄读答案，不求解题目；识别结果未经教师确认绝不进入画像。

---

## 1. M6 要交付什么

M6 不是再做一套批改系统，而是在 M4 已稳定的答案合同前增加图片输入层：

~~~text
发布快照
→ 可打印学生试卷与统一答题区
→ 教师选择准确实例并上传答题区照片
→ VLM 只提取最终答案
→ 教师对照原图确认或修正
→ M4 确定性规则判分
→ AttemptRevision 审计
→ 画像重算
→ M5 班级学情
→ 下一轮个性化组卷
~~~

### 1.1 本阶段必须完成

- 从 published PaperInstance 快照生成学生版打印页；
- 正文与答题区显示同一个 instance_code 和版本号；
- 学生版不包含标准答案、判分规则或推荐审计；
- 支持 JPEG、PNG、WebP 答题区图片上传；
- 支持教师按 instance_code 或明确 PaperInstance 选择准确版本；
- 建立 VisionService 抽象、真实 VLM 适配器和测试假实现；
- 严格校验模型输出，补齐 missing，拒绝重复或越界题号；
- 区分 readable、blank、missing、illegible、ambiguous；
- 图片识别生成的 Attempt 初始全部为 needs_review；
- 教师能并排查看原图与识别结果，逐题或批量确认；
- 确认后调用现有 grade_answer，不让 VLM 决定对错；
- 教师修正、确认和撤销继续追加 AttemptRevision；
- 重复上传、重试、补拍和并发提交不重复更新画像；
- 用真实照片评估负号、分数、空白、涂改、模糊和缺失题号；
- 报告准确率、漏识别率、需修改比例和复核耗时；
- VLM 超时或不可用时，手动录入仍可完成业务；
- 新增 verify_m6.py、后端测试、M6 Playwright 和交接文档。

### 1.2 明确不在第一版实现

- 不让 VLM 解题、判分或看到标准答案；
- 不根据姓名猜测学生，也不默认选择“最新试卷”；
- 不要求自动二维码识别；
- 不要求自动透视校正、多页批量扫描或独立答题卡；
- 不识别完整题干、步骤过程或复杂代数等价；
- 不开放基于 confidence 的自动确认；
- 不新增向量数据库、RAG、多 Agent、能力雷达、真实权限或多租户。

二维码可以后续增强，但不能挤占真实照片闭环和样本评估。

### 1.3 M6 完成标准

只有以下条件同时满足，才能写“教师演示版完成”：

1. 已发布试卷可打印或保存为 PDF，编号和题序与快照一致；
2. 使用真实照片调用真实 VLM，而不只是固定 JSON；
3. 模型请求中没有标准答案和真实姓名；
4. 模糊、缺失、歧义和真实空白不会被直接记错；
5. 教师确认后才产生有效画像证据；
6. 旧版照片按旧版 instance_code 和旧快照评分；
7. 重复上传、重试和并发确认不会重复增加 Attempt；
8. VLM 失败时可以无损切换到手动录入；
9. 有真实识别质量与教师复核负担报告；
10. M1～M6 验收、后端测试、前端构建和浏览器回归通过。

---

## 2. 开工检查

### 2.1 先封板 M5

开始 M6 前应保留独立 M5 Git 检查点，并实际跑通 m5_start 和 m5_showcase 隔离库。不要把未提交的 M5 工作与迁移、图片、VLM 代码混在一起。

### 2.2 优先阅读

~~~text
backend/app/models/entities.py
backend/app/schemas/submissions.py
backend/app/services/grading.py
backend/app/services/submission_workflow.py
backend/app/services/paper_workflow.py
backend/app/api/routes/papers.py
backend/app/api/routes/submissions.py
backend/app/core/config.py
backend/app/main.py
frontend/src/pages/PaperInstancePage.tsx
frontend/src/pages/PaperGradingPage.tsx
frontend/src/components/ManualAnswerEditor.tsx
frontend/src/types/submission.ts
docs/12_M5_工作总结与M6交接.md
~~~

### 2.3 直接复用的合同

- PaperInstance.instance_code 精确定位版本；
- PaperInstanceItem 的题目、答案和规则发布快照；
- ManualAnswerIn 的 item_id、order_no、response_status 和 answer；
- grade_answer 的 choice、exact、numeric、group 规则；
- 一份试卷一个 Submission、一道发布题一个 Attempt；
- needs_review 不进入画像；
- AttemptRevision 只追加；
- expected_revision_no 并发控制；
- persist_mastery 与全量 replay；
- M5 班级与作业聚合。

### 2.4 基线命令

~~~powershell
cd D:\desktop\math-agent\code\backend
$env:DATABASE_URL = 'postgresql+psycopg://postgres@localhost:55432/math_agent'
& '..\.conda-env\python.exe' -m pytest -q
& '..\.conda-env\python.exe' -m scripts.verify_m1
& '..\.conda-env\python.exe' -m scripts.verify_m2
& '..\.conda-env\python.exe' -m scripts.verify_m3
& '..\.conda-env\python.exe' -m scripts.verify_m4
& '..\.conda-env\python.exe' -m scripts.verify_m5
~~~

记录基线数量、构建体积和 warning。M6 不得通过放宽既有测试掩盖回归。

---

## 3. 十四条业务不变量

### 3.1 图片绑定准确版本

上传必须携带 paper_instance_id 和纸面 instance_code。后端重新查询并验证 paper、student、code 和版本状态。编号缺失或冲突时停止，不按学生最新试卷猜测。

### 3.2 旧版本按旧快照处理

V1 被 V2 supersede 后，若学生实际拿到 V1，教师精确输入 V1 短码，应允许 V1 首次提交并按 V1 快照评分。主动 withdrawn 版本默认禁止新提交。

M6 因而需要收窄调整 M4 的规则：仅对 superseded 且短码精确确认的历史版本开放，不能无条件放开失效版本。

### 3.3 VLM 不接触标准答案

请求只包含去身份化答题区图片、预期题号、题型、空序号和输出 schema。禁止包含 answer_snapshot、correct_answer、判分结果和易错分析。

### 3.4 VLM 只抄读

模型输出只回答“看到什么”和“不确定状态”。is_correct 必须由教师确认后的答案交给 grade_answer 产生。

### 3.5 confidence 不是正确概率

confidence 只记录模型自报值，不参与自动放行，也不能显示为“正确率”。

### 3.6 五种状态不得混淆

~~~text
readable    看到了明确答案
blank       明确看到了空白
missing     预期题号未出现
illegible   找到位置但无法辨认
ambiguous   多个可能答案或涂改后无法确定
~~~

null 不能自动解释为 blank。

### 3.7 教师确认前不更新画像

RecognitionRun 不是学习证据。提交为 image Submission 后，所有 Attempt 初始均为 needs_review、is_correct=null；此时 confirmed 数和 mastery 不得变化。

### 3.8 教师确认抄读，规则决定对错

教师确认 readable 或 blank 后调用 grade_answer。若规则仍不能可靠判断，Attempt 继续 needs_review，再使用 M4 人工判定。

### 3.9 原始结果不可覆盖

模型原始 answer、status、confidence 保留。教师修正写入 Attempt 当前状态并追加 revision，记录 recognition_run_id、模型版本和原因。

### 3.10 重识别不能覆盖已确认结论

提交前可以补拍并选择新 run。Submission 已创建后，新识别结果不得自动覆盖 teacher_confirmed Attempt。

### 3.11 一份试卷仍只有一组证据

Submission.paper_instance_id 唯一和 Attempt.paper_instance_item_id 唯一继续成立。重复请求重放已有结果或返回 409。

### 3.12 批量确认必须单事务

先锁定 Student、Submission 和所有目标 Attempt，逐项验证 revision_no，统一写修订并只重算一次画像。任一冲突整体回滚。

### 3.13 文件与审计分离

原图可按策略删除；图片哈希、模型版本、识别状态、Submission、Attempt 和 revision 必须保留。

### 3.14 手动录入永远可用

上传、模型调用或解析失败不得锁死试卷。尚未创建 Submission 时，教师仍可使用 M4 手动录入。

---

## 4. 打印试卷与统一答题区

### 4.1 第一版形式

推荐新增浏览器打印页：

~~~text
/paper-instances/:paperInstanceId/print
~~~

通过 A4 打印 CSS 提供“打印 / 保存为 PDF”。若教师明确需要一键下载文件，再在同一数据合同上增加服务端 PDF，不必先引入沉重渲染依赖。

### 4.2 安全打印 API

新增独立学生视图，不能复用可能返回标答的教师接口：

~~~text
GET /api/v1/paper-instances/{paper_id}/print-view
~~~

响应只含 paper、instance_code、version、student_code、layout_version、题目可见内容、资源和 answer_slots。禁止出现：

~~~text
answer_snapshot
answer_rule_snapshot
correct_answer
grading_details
selection_audit
推荐分数
~~~

### 4.3 布局要求

- 页眉显示作业、匿名学生编号、版本和短码；
- 每页页脚重复短码；
- 正文严格按 PaperInstanceItem.order_no；
- 文末或最后一页放统一答题区；
- 选择题留单字符格，填空题留横线；
- 多空题显示题号和 blank_id；
- 答题区再次打印短码和版本；
- 草稿显著标记“未发布”，不能作为正式学生版；
- 历史版本可打印，但必须显示真实版本。

### 4.4 answer_region

第一版由教师上传手动裁剪的统一答题区，不用 answer_region 自动裁图。模板坐标只有在图片完成方向、缩放和透视对齐后才能用于裁剪，自动对齐后置。

### 4.5 打印验收

- 正文和答题区短码一致；
- 题序、空序与发布快照一致；
- V1、V2 不混；
- HTML、接口和 PDF 均无标答泄漏；
- 题目图片可见；
- A4 无明显截断或横向溢出。

---

## 5. 数据库增量

新增 Alembic 0013，不修改旧迁移。

### 5.1 recognition_runs

建议字段：

~~~text
id UUID PK
paper_instance_id UUID FK NOT NULL
student_id UUID FK NOT NULL
submission_id UUID FK NULL UNIQUE
idempotency_key VARCHAR(100) UNIQUE NOT NULL
request_fingerprint CHAR(64) NOT NULL
status VARCHAR(32) NOT NULL
provider VARCHAR(64) NOT NULL
model VARCHAR(160) NOT NULL
prompt_version VARCHAR(80) NOT NULL
image_storage_key VARCHAR(500) NOT NULL
image_sha256 CHAR(64) NOT NULL
original_filename VARCHAR(255) NULL
content_type VARCHAR(80) NOT NULL
byte_size BIGINT NOT NULL
pixel_width INTEGER NOT NULL
pixel_height INTEGER NOT NULL
raw_response JSONB NULL
error_code VARCHAR(80) NULL
error_message TEXT NULL
started_at TIMESTAMPTZ NULL
completed_at TIMESTAMPTZ NULL
committed_at TIMESTAMPTZ NULL
retention_until TIMESTAMPTZ NULL
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
~~~

状态限定为 uploaded、processing、ready_for_review、failed、committed、discarded。图片二进制不存数据库。

### 5.2 recognition_items

~~~text
id UUID PK
recognition_run_id UUID FK NOT NULL
paper_instance_item_id UUID FK NOT NULL
order_no INTEGER NOT NULL
recognition_status VARCHAR(24) NOT NULL
recognized_answer JSONB NULL
confidence NUMERIC(5,4) NULL
provider_payload JSONB NULL
created_at TIMESTAMPTZ NOT NULL
~~~

唯一约束：

~~~text
(recognition_run_id, paper_instance_item_id)
(recognition_run_id, order_no)
~~~

readable 必须有答案；blank 不得带非空答案；confidence 只能为 null 或 0～1。成功提取后，每道发布题必须有一条 item，缺项由后端补 missing。

### 5.3 继续复用

不重建图片版 Attempt。继续使用 Submission.source=image、Submission.attachments、Attempt.ocr_confidence、Attempt.recognition_status、Attempt.grade_status 和 AttemptRevision。

attachments 只保存 run_id、哈希和受控访问地址，不暴露绝对磁盘路径。

### 5.4 数据库守卫

测试 run/item/paper 归属、run 单次提交、Submission 单个 committed run、teacher_confirmed 不被重识别覆盖、revision 只追加，以及删除图片不级联删除证据。

---

## 6. 图片接收与存储

### 6.1 依赖和配置

建议增加 python-multipart、Pillow、httpx，并配置：

~~~text
RECOGNITION_UPLOAD_DIR=/app/data/uploads/m6
RECOGNITION_MAX_UPLOAD_BYTES=8388608
RECOGNITION_MAX_PIXELS=20000000
RECOGNITION_RETENTION_DAYS=30
VISION_PROVIDER=...
VISION_MODEL=...
VISION_API_BASE_URL=...
VISION_API_KEY=
VISION_TIMEOUT_SECONDS=45
VISION_ENABLED=false
~~~

密钥只通过环境变量提供，不进入仓库、数据库、响应或日志。

### 6.2 校验步骤

1. 流式读取并限制字节数；
2. 计算 SHA-256；
3. 用 Pillow 检查真实格式；
4. 限制像素总量；
5. 应用 EXIF 方向；
6. 转换为 RGB JPEG 或 PNG；
7. 去除 EXIF；
8. 使用服务端 storage_key；
9. 临时文件写完后原子改名；
10. 数据库失败时清理孤立临时文件。

第一版拒绝 PDF、HEIC、SVG、GIF 动图和任意二进制。

### 6.3 受控读取与清理

不要把上传目录挂为公开 StaticFiles。通过以下接口按 run 查询：

~~~text
GET /api/v1/recognition-runs/{run_id}/image
~~~

新增 purge_recognition_images.py，默认 dry-run，显式确认后才删除过期文件。删除后保留审计元数据。

---

## 7. VisionService 与输出解析

### 7.1 模块

~~~text
app/services/vision/base.py
app/services/vision/fake.py
app/services/vision/provider_x.py
app/services/vision/parser.py
app/services/recognition_workflow.py
app/schemas/recognition.py
app/api/routes/recognition.py
~~~

业务层只依赖 VisionService 接口，不直接依赖供应商 SDK。测试统一注入 FakeVisionService，不访问真实网络。

### 7.2 模型输入

只发送图片与类似以下期望槽位：

~~~json
[
  {"order_no": 1, "question_type": "choice", "blank_ids": ["answer"]},
  {"order_no": 2, "question_type": "fill_blank", "blank_ids": ["answer"]}
]
~~~

### 7.3 标准输出

~~~json
{
  "schema_version": "m6-recognition-v1",
  "answers": [
    {
      "order_no": 1,
      "answer": "B",
      "recognition_status": "readable",
      "confidence": 0.98
    },
    {
      "order_no": 2,
      "answer": null,
      "recognition_status": "illegible",
      "confidence": 0.35
    }
  ]
}
~~~

### 7.4 严格解析

- 根对象、schema_version 和 answers 类型正确；
- order_no 是严格整数且不能重复；
- 题号必须属于当前发布快照；
- 状态必须在允许集合；
- confidence 为 null 或 0～1；
- readable 的 answer 非空；
- blank 的 answer 为空；
- 未返回题目补 missing；
- 未知或重复题号使整次 run failed；
- 不从自然语言解释中猜答案；
- raw_response 脱敏并限制大小。

错误码至少区分 vision_disabled、vision_timeout、vision_network_error、vision_provider_error、vision_invalid_json、vision_schema_error 和 image_unreadable。

---

## 8. 识别与确认事务

### 8.1 API

~~~text
POST   /api/v1/paper-instances/{paper_id}/recognition-runs
POST   /api/v1/recognition-runs/{run_id}/extract
GET    /api/v1/recognition-runs/{run_id}
GET    /api/v1/recognition-runs/{run_id}/image
POST   /api/v1/recognition-runs/{run_id}/commit
POST   /api/v1/submissions/{submission_id}/recognition-review
DELETE /api/v1/recognition-runs/{run_id}
~~~

上传为 multipart，至少包含 file、idempotency_key、instance_code。

### 8.2 上传

校验图片，锁定并核对 PaperInstance 与短码，计算请求指纹，处理幂等，保存文件并创建 uploaded run。相同 key 和指纹重放已有 run；相同 key 不同指纹返回 409。

### 8.3 提取

先把 run 状态提交为 processing，再在数据库事务外调用模型，最后在短事务中写 items 和 ready_for_review 或 failed。不能持有学生、Submission 或 Attempt 行锁等待网络。

### 8.4 commit 为 image Submission

run 必须 ready_for_review，短码仍匹配，尚未提交，paper 尚无 Submission，items 完整覆盖。

一个事务中：

1. 锁定 Student、PaperInstance 和 run；
2. 创建 source=image Submission；
3. attachments 记录 run 和图片哈希；
4. 每题创建 Attempt；
5. readable 保存模型答案，其他状态保存 null；
6. recognition_status 和 confidence 原样保存；
7. 全部 grade_status=needs_review、is_correct=null；
8. 每题追加 revision 1，operator_type=vision；
9. run 关联 submission 并改 committed；
10. 不产生有效画像证据。

完成后待复核数增加，但 confirmed_attempt_count 和 mastery 不变。

### 8.5 批量确认

请求携带 reason、actor，以及每题 attempt_id、expected_revision_no、action、response_status 和 answer。

服务必须：

- 验证所有 Attempt 属于同一 image Submission；
- 稳定顺序锁定并校验修订号；
- accept 使用识别答案，correct 使用教师修正答案；
- missing、illegible、ambiguous 不能直接 accept；
- blank 必须由教师明确确认；
- 对确认答案调用 grade_answer；
- 可确定时写 teacher_confirmed；
- 规则仍不确定时继续 needs_review；
- 每项追加 revision；
- 整批只重算一次画像；
- 任一冲突整体回滚。

教师在这里确认的是“抄录是否正确”，不是手工选择题目对错。规则无法判断时再使用 M4 人工改判。

---

## 9. 前端

### 9.1 路由

~~~text
/paper-instances/:paperInstanceId/print
/paper-instances/:paperInstanceId/recognize
/recognition-runs/:runId/review
~~~

试卷详情增加“学生版打印”和“上传答题区照片”。批改页继续展示最终 Attempt 和修订。

### 9.2 上传页

显示匿名学生编号、作业、版本和系统短码；要求核对纸面短码；本地预览图片；提示先裁掉姓名；展示大小和格式；提供上传、识别、失败重试和手动录入入口。

### 9.3 核对页

桌面左侧原图、右侧逐题识别结果；移动端上下排列。每题显示状态、原始答案、confidence（明确“模型自报”）、教师最终值、操作和修订号。

允许批量确认 readable 项，但 blank 必须明确选择，missing、illegible、ambiguous 不进入“一键确认清晰项”。状态不能只靠颜色表达。

### 9.4 批改页补充

显示来源、原图入口、recognition_status、confidence、provider、model、prompt_version、模型原始答案和教师修正。图片清理后显示“原图已按策略删除”，审计仍可查看。

### 9.5 必备异常态

未选图片、格式不支持、过大、像素超限、短码不匹配、VLM 未配置、超时、结构错误、无有效答案、已有 Submission、run 已提交、图片已删除和网络失败。

---

## 10. 隐私、安全与日志

- 只用虚拟学生或匿名编号；
- 优先上传裁剪答题区；
- 不向模型发送姓名、学号、班级、标答或画像；
- 日志不打印 base64、模型完整原文、API key 或磁盘路径；
- 上传目录不公开；
- 验证真实 MIME、字节、像素和完整性；
- 文件名由服务端生成并防路径穿越；
- 去除 EXIF；
- 外部请求有连接和总超时；
- 供应商响应有大小上限；
- README 记录保存位置、保留天数、dry-run 与确认删除方法。

---

## 11. 真实样本评估

建议新增：

~~~text
data/eval/m6/manifest.json
data/eval/m6/images/
backend/scripts/evaluate_m6_recognition.py
~~~

真实学生图片不得提交仓库。仓库只保存匿名样本或 manifest 模板。

样本覆盖选择题、负号、整数、小数、分数、真实空白、涂改、模糊、缺失题号和拍摄角度不理想。

### 11.1 比较规则

识别评估只做 Unicode NFKC、首尾空白、常见减号、选择题大写和无意义空格等轻量规范化。不能把 1/2 与 0.5 因数学等价判为抄读正确。

### 11.2 指标

~~~text
提取准确率 = 完全正确的答案槽 / 全部人工真值答案槽
漏识别率   = 可辨认但输出 missing 或 illegible 的槽 / 可辨认槽
需修改比例 = 教师修改答案或状态的槽 / 全部槽
状态准确率 = 五种状态分类正确数 / 全部槽
复核耗时   = 打开核对页到完成确认的秒数
~~~

报告样本量、供应商、模型、prompt_version、日期、超时和失败数。复核耗时至少给中位数与最大值。Fake 用于 CI，但不能替代真实验收。

---

## 12. 建议实施顺序

### 批次 A：合同与迁移

写 recognition schemas、0013、数据库守卫、严格 parser 测试，以及 superseded 旧版精确短码规则。

### 批次 B：打印

实现 print-view、A4 页面、统一答题区、无标答泄漏测试和 V1/V2 测试。

### 批次 C：上传存储

实现配置、Compose 持久化、文件校验、EXIF 清理、受控读取和 dry-run 清理。

### 批次 D：VisionService

实现接口、Fake、parser、一个真实适配器、超时和脱敏日志。测试不访问网络，手工命令能识别匿名真实图。

### 批次 E：图片 Submission

实现 upload、extract、get、commit；验证全部 needs_review、画像不变、重复与并发守卫、历史 V1 精确提交。

### 批次 F：教师核对

实现原图并排、逐题与批量确认、规则判分、revision 和单次画像重算，并检查 M5 指标变化。

### 批次 G：真实评估

准备匿名样本、人工真值、固定模型与 prompt，运行评估并记录复核耗时。

### 批次 H：验收交接

实现 verify_m6，运行完整回归，更新演示场景、README、M6 总结和评估报告。

---

## 13. 测试矩阵

### 13.1 打印

- published 正式打印，草稿显示未发布；
- 短码在正文和答题区一致；
- 题序和空序正确；
- V1/V2 各自正确；
- API、HTML、PDF 无标答；
- 图片资源可打印，A4 无截断。

### 13.2 上传与解析

- 正常 JPEG、PNG、WebP；
- 假扩展名、损坏图片、不支持 MIME；
- 字节或像素超限、EXIF 旋转、路径穿越名；
- 同 key 同文件幂等、不同文件 409；
- readable、blank、missing、illegible、ambiguous；
- 重复或越界题号、confidence 越界、非法 JSON、超大响应；
- 多空题空序。

### 13.3 版本与幂等

- 活跃 V1 正常；
- V1 被 V2 替代后，准确 V1 码仍按 V1；
- 选择 V2 但输入 V1 码被拒绝；
- withdrawn 禁止新提交；
- 手动与图片 Submission 相互冲突；
- 同一 run 只能 commit 一次。

### 13.4 画像安全

- run 各暂存状态均不改变画像；
- image Submission 全 needs_review 时画像不变；
- 确认一题只增加一条有效证据；
- missing、illegible、ambiguous 不计错；
- 确认 blank 后按规则处理；
- 批量确认只重算一次；
- 并发确认一个成功、一个 409；
- 重识别不覆盖已确认；
- 撤销后与 replay 一致。

### 13.5 故障与 E2E

- VISION_ENABLED=false、无 key、网络错误、超时、429、5xx、非法 JSON；
- 失败后能重试或手动录入；
- 浏览器覆盖打印、上传、Fake 返回多状态、原图核对、修正、提交、画像变化、重复点击；
- 1440px 与 390px 无全局横向溢出。

---

## 14. verify_m6.py

脚本默认只读，不调用外部模型，不修改数据。输出 JSON，非零退出码表示失败。

至少检查：

~~~text
schema_head
printable_published_papers
recognition_run_count
ready_run_count
failed_run_count
committed_run_count
image_submission_count
image_attempt_count
pending_image_attempt_count
confirmed_image_attempt_count
missing_recognition_items
duplicate_run_items
run_paper_ownership_errors
submission_run_link_errors
confirmed_without_revision_errors
recognition_profile_leak_errors
missing_image_files
expired_image_files
mastery_replay_mismatches
validation_errors
~~~

增加严格演示模式：

~~~powershell
python -m scripts.verify_m6 --require-image-scenario
~~~

该模式必须存在至少一个真实图片闭环，不能只给 warning。真实质量评估由独立脚本运行，不放进普通 CI。

---

## 15. API 错误约定

~~~text
400  multipart 或文件不可读
404  paper、run、submission 或文件不存在
409  幂等、已有 Submission、revision 或 run 状态冲突
413  超过字节限制
415  格式不支持
422  短码不匹配、版本不允许、模型结构错误、确认不完整
502  上游模型错误
504  模型超时
~~~

错误响应保持现有 code、message、details 结构，不暴露供应商正文、密钥和磁盘路径。

---

## 16. 完成后的命令

### 16.1 后端

~~~powershell
cd D:\desktop\math-agent\code\backend
$env:DATABASE_URL = 'postgresql+psycopg://postgres@localhost:55432/math_agent'
& '..\.conda-env\python.exe' -m alembic upgrade head
& '..\.conda-env\python.exe' -m pytest -q
& '..\.conda-env\python.exe' -m scripts.verify_m1
& '..\.conda-env\python.exe' -m scripts.verify_m2
& '..\.conda-env\python.exe' -m scripts.verify_m3
& '..\.conda-env\python.exe' -m scripts.verify_m4
& '..\.conda-env\python.exe' -m scripts.verify_m5
& '..\.conda-env\python.exe' -m scripts.verify_m6
~~~

### 16.2 前端

~~~powershell
cd D:\desktop\math-agent\code\frontend
npm run typecheck
npm run build
npm run test:e2e
~~~

### 16.3 真实评估

~~~powershell
cd D:\desktop\math-agent\code\backend
& '..\.conda-env\python.exe' -m scripts.evaluate_m6_recognition --manifest '..\data\eval\m6\manifest.json' --output '..\data\eval\m6\results.json'
~~~

输出不得回显密钥，并记录模型 ID、prompt_version 和时间。

---

## 17. 教师演示路径

建议 10～12 分钟：

1. 打开已发布试卷；
2. 展示学生打印页和相同短码；
3. 展示保存的 PDF，说明学生版无标答；
4. 核对纸面短码并上传真实答题区照片；
5. 开始提取，说明模型只抄读；
6. 展示 readable、blank 和不确定状态；
7. 批量确认清晰题，手工修正一题；
8. 提交后由规则判分；
9. 查看 revision 和图片来源；
10. 返回学生画像与班级学情查看变化；
11. 再组卷并解释最新证据；
12. 演示模型失败时切换手动录入。

主动说明：图片为匿名演示数据；confidence 不是正确概率；教师确认前不更新画像；第一版不自动识别二维码和透视；效果以小样本报告为准；复杂等价和步骤给分未实现。

---

## 18. 交付产物

### 18.1 后端

- 0013 migration；
- RecognitionRun、RecognitionItem；
- 安全打印 API；
- 上传、存储、清理服务；
- VisionService、Fake 和真实适配器；
- parser 与 recognition workflow；
- 图片批量确认；
- verify_m6 和真实评估脚本；
- 单元、事务、API、数据库守卫测试。

### 18.2 前端

- 学生打印页；
- 图片上传页；
- 原图并排核对页；
- 批量确认；
- 图片来源和识别元数据；
- 完整异常与手动兜底；
- 桌面、390px 和 M6 Playwright。

### 18.3 数据与文档

- 匿名真实样本或安全 manifest；
- 人工 ground truth；
- 真实模型评估结果；
- 图片保留与清理说明；
- 更新后的固定演示场景；
- README；
- M5 教师演示脚本；
- M6 工作总结与教师演示版交接；
- M6 真实图片识别评估报告。

---

## 19. 常见返工点

1. 直接把 VLM 输出交给 grade_answer，导致识别错误污染画像；
2. 把 null 当空白；
3. 把 confidence 当自动确认阈值；
4. 把标准答案放进提示词；
5. 根据学生选择最新试卷；
6. 完全禁止 superseded 旧版首次提交；
7. 等待 VLM 时持有数据库锁；
8. 只靠前端防重复；
9. 重识别覆盖 teacher_confirmed；
10. 上传目录公开暴露；
11. 把大图片二进制存 PostgreSQL；
12. 用数学等价替代抄读准确率；
13. 只有 Fake VLM，却声称真实识别完成；
14. 删除图片时同时删除作答审计。

---

## 20. 最终验收清单

### 打印与版本

~~~text
□ 正式打印来自 published 快照。
□ 正文和答题区使用相同 instance_code。
□ 题号和空序与 PaperInstanceItem 一致。
□ 学生版无标答和判分规则。
□ V1、V2 分别定位和打印。
□ superseded V1 精确确认后按 V1 批改。
□ 编号异常时暂停，不猜版本。
~~~

### 图片与 VLM

~~~text
□ 校验真实 MIME、大小、像素和完整性。
□ 图片去 EXIF且不公开目录。
□ 模型请求无真实身份和标准答案。
□ 真实适配器可用，测试使用 Fake。
□ 输出严格校验并补 missing。
□ 五种识别状态明确区分。
□ confidence 不自动确认。
□ 超时、5xx、非法 JSON 可重试或手动兜底。
~~~

### 复核与画像

~~~text
□ 图片 Attempt 初始全部 needs_review。
□ 教师确认前 confirmed 和 mastery 不变。
□ 接受或修正后调用 grade_answer。
□ 批量确认使用单事务和 revision_no。
□ 每次变化追加 AttemptRevision。
□ 不确定项不误记成错题。
□ 重识别不覆盖教师确认。
□ 重复上传、补拍和并发不重复增加证据。
□ 撤销后画像与 replay 一致。
□ M5 聚合指标定义未改变。
~~~

### 真实评估与工程质量

~~~text
□ 样本覆盖选择、负号、分数、空白、涂改、模糊和缺失题号。
□ 有人工 ground truth。
□ 记录供应商、模型、prompt、日期和样本量。
□ 报告准确率、漏识别率、修改比例和复核耗时。
□ Fake 结果不计入真实验收。
□ Alembic 0013 可从 0012 升级。
□ M1～M6 verify 均为 0 个错误。
□ 后端 pytest、前端 typecheck、build 和 E2E 通过。
□ 清理脚本默认 dry-run。
□ 密钥、base64 和磁盘路径未进入日志。
□ 模型完全不可用时手动闭环仍可演示。
~~~

---

## 21. 现在最先做的一件事

先不要接真实模型，也不要先画上传页。第一步写 0013、recognition schemas 和以下测试：

~~~text
1. 缺题补 missing，重复或越界题号整次失败；
2. image Submission 创建后全部 needs_review，画像不变；
3. 教师确认后才调用 grade_answer，并且整批只重算一次画像；
4. superseded V1 只有 instance_code 精确匹配时才能首次提交。
~~~

四组测试固定后，再实现打印、上传和真实 VisionService。它们决定 M6 是否安全，不能由前端或供应商行为临时定义。

---

## 22. 后续实现：M6.1 独立答题卡与自动定位

### 22.1 当前边界说明

当前 M6 打印的是“学生试卷正文 + 文末统一答题区”，并非独立答题卡。第一版要求教师手工裁剪统一答题区后上传，VLM 读取整块区域。该方案用于完成教师演示版最小闭环，不应被描述为已经支持标准化答题卡、自动逐题定位或真实课堂批量扫描。

### 22.2 M6.1 目标

从同一 published PaperInstance 快照生成独立单页答题卡，使学生答案与题号的对应关系由固定模板保证，而不是依赖模型从任意照片中猜测。目标链路为：

~~~text
发布快照
→ 学生试卷 + 独立答题卡
→ 手机拍摄整张答题卡
→ 定位标记检测与透视矫正
→ 按 answer_region 逐题裁剪
→ 一个主 VLM 抄读答案
→ 教师对照整页原图和裁剪图确认
→ 复用 M4 判分、审计和画像链路
~~~

### 22.3 打印与布局要求

- 增加“试卷 + 答题卡”和“仅答题卡”打印入口；
- 单页 A4 优先，题量超出时使用明确页码和总页数；
- 页眉打印匿名学生编号、作业、`instance_code`、`version_no` 和 `layout_version`；
- 四角加入稳定定位标记；二维码为可选增强，短码仍作为人工兜底；
- 选择题、单空题和多空题使用不同但固定的答题框；
- 每个框保存页码、`order_no`、`blank_id` 和归一化坐标；
- 字体、边距、分页或框尺寸变化时必须升级 `layout_version`；
- 答题卡接口继续使用学生字段白名单，禁止返回标答、解析或教师字段。

### 22.4 后端新增状态与审计

建议增加或明确以下处理中间状态：

~~~text
uploaded
→ alignment_processing
→ needs_manual_crop / aligned
→ extracting
→ ready_for_review
~~~

识别记录需保存页面方向、定位结果、透视变换版本、模板版本和每题裁剪坐标。裁剪图不能替代原图；教师复核和问题排查必须能从 RecognitionItem 回到原始整页图片。

### 22.5 失败处理

- 定位标记不足、页面被截断、页码缺失：要求重拍或人工裁剪；
- 二维码与纸面短码冲突：暂停，不猜测版本；
- 题框越界或裁剪为空：标记 missing，不直接判错；
- 多页缺页：整份保持待处理，不静默提交不完整答案；
- 自动处理完全不可用：继续保留当前手工裁剪和 M4 手动录入入口。

### 22.6 模型数量原则

M6.1 默认仍只接一个主视觉模型。先通过固定答题卡和逐题裁剪提升输入质量，再根据真实评测决定是否把 4B 替换为 8B，或针对明确短板试验专用 OCR。PP-OCRv5、PP-FormulaNet、Embedding、RAG 和普通对话 LLM 都不是独立答题卡第一版的前置依赖。

### 22.7 M6.1 验收清单

~~~text
□ 独立答题卡来自 published 快照，和学生试卷使用同一短码、版本和题序。
□ layout_version 与 answer_region 坐标被冻结且可复现。
□ HTML、PDF 和接口均无标答、解析或教师字段泄漏。
□ 正放、旋转、轻度透视照片可完成页面对齐与逐题裁剪。
□ 无法定位时进入人工处理，不错配学生、试卷、题号或空序。
□ 每个识别结果可追溯到原图、页码、裁剪区域和 PaperInstanceItem。
□ 教师确认前不判分、不更新画像，confidence 仍不自动放行。
□ 使用匿名真实样本比较 M6 与 M6.1 的识别质量和复核时间。
□ 在评测证明必要前，不引入额外专用模型和重型依赖。
~~~

更详细的模型决策和开发批次见 `11_数学教学Agent_M6模型接入与答题识别优化计划.md` 第 7 节。
