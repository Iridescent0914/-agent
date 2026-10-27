# 第一步开发指导：M0 有理数题库摸底与首批数据准备

> 版本：v0.1  
> 日期：2026-09-23  
> 对应规划：[03_数学教学Agent_Demo开发步骤规划.md](03_数学教学Agent_Demo开发步骤规划.md) 第 3 节（M0）。  
> 本文是执行指导，尚未完成题库整理，也未创建下文列出的脚本或种子数据。

## 1. 这一步要完成什么

把现有“有理数”资料整理成一批**能完整展示、能找到原始出处、能按明确规则判分的结构化题目**，为后续数据库导入、组卷和批改做准备。

先跑通 5–10 道题的整理与核对流程，再扩展到目标 30–50 道，至少覆盖 5 个考点。这里的“道”按原始题目 ID 计数，不把一个 ID 下的小问算成多个独立题目。

完成顺序：

```text
检查原始资料
→ 提取 139 条原始记录与问题清单
→ 挑选 5–10 道候选题试整理
→ 核对题干、答案、标签和判分要求
→ 固定最小数据格式与考点映射
→ 扩展到 30–50 道
→ 生成核对页、校验报告和交接说明
```

本阶段写小型数据处理脚本即可。数据库、React、FastAPI、学生画像、OCR 和模型接口在后续阶段实现；M0 不需要先安装整套业务依赖。

## 2. 当前资料已经确认的情况

以下来自本次对本地 DOCX/XML 和 CSV 的检查，尚未完成逐题可视核对。

### 2.1 使用哪些文件

| 用途 | 工作区相对路径 | 使用方法 |
|---|---|---|
| 题目内容主来源 | `六年级_数学_校本审核/rational_有理数_校本审核（黄）.docx` | 提取题干、答案、题型、难度、KP、考点和易错分析 |
| 题目索引 | `六年级_数学_校本审核/rational_有理数_manifest.csv` | 按 qid 关联课时、原题号及索引标记 |
| 辅助对照资料 | `六年级_数学_demo9_审核/六年级_数学_demo9_审核/01_有理数.docx` | 主来源内容有疑问时辅助核对，记录版本差异，不静默替换 |
| 辅助 ID 资料 | `六年级_数学_demo9_审核/六年级_数学_demo9_审核/id_对照表_六年级数学.xlsx` | 需要跨资料定位时再查看，先确认其列结构与 ID 含义 |

当前确实存在有理数 manifest，不需要重新编造题目 ID。排除 `__MACOSX` 中的 `._*` 文件。

### 2.2 实际统计

| 检查项 | 当前结果 | 对开发的影响 |
|---|---:|---|
| manifest 记录数 | 139 | 是索引条目数，不等于 139 道独立单答案题 |
| DOCX 顶层元数据表 | 139 | 可作为题块划分的主要锚点，还需核验 qid 是否一一对应 |
| manifest 题型 | 填空 62、选择 2、解答 74、判断 1 | 只能作为预筛选参考，不能据此断定实际只有 2 道选择题 |
| manifest 标记含公式 | 104 条 | 完全排除含公式题可能严重减少候选 |
| manifest 标记含图片 | 20 条 | 仍需结合 DOCX 实际对象检查 |
| DOCX 原生数学对象 `m:oMath` | 131 个 | 对象数不等于含公式题数 |
| DOCX 包内 `word/media/` 项 | 563 个 | 不能等同于 563 张题干图，需按引用关系定位 |

manifest 列为：

```text
qid, section, qnum, type, has_equation, has_image, preview
```

它没有完整标答、考点或可直接用于展示的完整题干，不能把 preview 当成正式 question_text。

### 2.3 必须纳入问题清单的真实例子

| 原始 qid 后缀 | 本次观察 | 整理时如何处理 |
|---|---|---|
| `.001` | 原始记录包含 8 个小问，部分小问又有多个空 | 标记多问多空，不能当作单答案填空题直接导入 |
| `.002` | manifest 和元数据题型为“填空题”，正文是含 4 个小问的选择题；易错分析仍有“AI 草拟，请核对” | 记录题型冲突、多问结构和审核标记；核实后再决定能否入选 |
| `.037` | 正文含 3 个选择小问；标题显示两星，元数据表显示三星 | 保留两处原值，记录冲突，不擅自选一个覆盖 |
| `.038` | 计算题存在公式对象，纯文本提取的题干不完整，参考答案文本区为空 | 暂不入首批；可视核对是否有公式、图片形式的答案，不能仅凭空文本断定原文没有答案 |

本地文档还保留了“AI 草拟，请核对”等提示。它们**不证明老师没有审核过**，但现有文件也不足以自动证明每条内容均已确认。应把题干、标答和易错分析的核对状态分开记录；标答有疑问时再请教师确认，不能让模型补一个答案后直接标为 verified。

## 3. 工作目录和最终产物

当前 `code/` 目录为空。后续代码和数据统一放在此目录下，原始资料留在现有位置只读使用。

建议 M0 形成：

```text
code/
├── scripts/
│   ├── inspect_rational_sources.py
│   ├── extract_rational_questions.py
│   ├── validate_rational_seed.py
│   └── render_rational_review.py
└── data/
    ├── staging/
    │   ├── rational_raw.json
    │   └── rational_candidates.json
    ├── seed/
    │   ├── rational_questions.json
    │   └── rational_knowledge_points.json
    └── import_audit/
        ├── rational_source_report.md
        ├── rational_import_audit.csv
        ├── rational_review.html
        └── rational_validation_report.json
```

这些是**待开发的产物**，不是目前已存在的文件。

| 产物 | 内容 |
|---|---|
| source_report | 来源路径、SHA-256、条目数、字段分布、版本差异与数据缺口 |
| raw | 尽量忠实保存的抽取结果、原始元数据、位置与对象标记 |
| candidates | 经过筛选、仍可能有问题的候选题，不直接供业务系统使用 |
| questions | 只包含通过核对且满足首版规则的正式种子题 |
| knowledge_points | 章—节—知识点—考点及原标签映射 |
| import_audit | 每题选用/暂缓理由、修正记录和确认依据 |
| review.html | 对照核对用的题目、答案、来源和问题展示页 |
| validation_report | 正式种子是否符合约束、题型/难度/考点分布和未解决问题 |

脚本用项目路径定位文件，不依赖你从哪个终端目录启动。例如脚本位于 `code/scripts/` 时：

```python
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = CODE_ROOT.parent
SOURCE_DIR = WORKSPACE_ROOT / "六年级_数学_校本审核"
DATA_DIR = CODE_ROOT / "data"
```

JSON 使用 UTF-8；面向 Excel 的核对 CSV 可使用 UTF-8 BOM。读取 CSV 时按文本处理 qid、负数和分数字符串，避免 Excel 将其转成其他格式。正式种子以 JSON 为准。

## 4. 具体执行步骤

### 步骤 A：先做资料摸底

先开发 `inspect_rational_sources.py`。只读来源文件，输出来源报告，不生成正式题库。

首版使用 Python 标准库即可：

- `csv`：读取 manifest。
- `zipfile`、`xml.etree.ElementTree`：检查 DOCX 的 XML、数学对象和图片引用。
- `hashlib`：记录来源文件 SHA-256。
- `json`、`collections`：输出统计。

脚本至少完成：

1. 检查两个主要来源文件存在，记录路径、文件大小和 SHA-256。
2. 读取 manifest，检查 qid 重复、空值、题型和课时分布。
3. 提取 DOCX 中元数据表的 qid，检查与 manifest 的集合是否一致。
4. 统计原始题型、星级、KP、考点、数学及图像对象。
5. 列出题型冲突、疑似多小问、答案空文本、待核对标记等问题。
6. 原样保留异常，不把“疑似有问题”直接当作“原题错误”。

**完成标志：**你能解释 139 条是什么，以及为什么不能直接对 CSV 按“选择/填空”过滤后入库。

### 步骤 B：按 DOCX 结构提取原始题块

当前 DOCX 的常见排列为：

```text
题目标题
→ 元数据表（含题目 ID、题型、难度、KP、考点等）
→ “题干”标题及其内容
→ “参考答案”标题及其内容
→ “易错分析”标题及其内容
→ 下一题标题
→ 下一题元数据表
```

抽取时按 `word/document.xml` 的 body 子节点顺序遍历，保留段落与表格的相对位置。只分别读取“所有段落”和“所有表格”再拼接，会丢失归属关系。

推荐处理逻辑：

1. 用“含题目 ID 字段的表格”识别题目元数据，不把每张表都当成新题。
2. 以原始 qid 建立题块，再关联 manifest 的 section 和 qnum。
3. 使用独立段落标题“题干”“参考答案”“易错分析”划分内容区。
4. 下一题标题不能混入上一题易错分析；结合标题段落及其后的元数据表识别边界，不能按任意数字开头的段落切题。
5. 保存原始元数据、标题、内容区、数学/图像对象位置和来源定位。
6. 抽取失败或出现无法解释的结构时记录 parse_issue，转人工处理。

raw 中至少记录：

```text
original_qid
source_file
source_sha256
source_locator.metadata_table_index（从 1 开始）
source_metadata（原标签，不覆盖）
source_title
manifest_row（原始索引行）
stem_blocks / answer_blocks / analysis_blocks
contains_omml / contains_drawing / contains_ole
parse_issues
```

这里的对象检测要按题块检查，不能只依赖 manifest 的 has_equation/has_image。抽取脚本重复运行只更新 staging，不能覆盖已人工核对的 seed 或核对记录。

**完成标志：**每条候选记录能定位回原 DOCX 中的具体 ID；提取出的题干、答案和易错分析没有串题。

### 步骤 C：处理公式和图像

最容易出错的是：文本看起来能读，但负号、分母、指数或整段公式已经丢了。

处理优先级：

1. **普通文本**：保留段落、选项顺序和数学符号。
2. **简单公式**：核对 Word 中的真实显示后，转成可读的 Unicode、明确括号表达式或 LaTeX。
3. **复杂公式和嵌入对象**：保留对象与来源位置，首批可暂缓，不要求一次写出通用转换器。
4. **图形依赖题**：无法完整保存并核对图形时，不进入首批正式种子。

注意：

- 拼接 `w:t` 能得到普通文本，但不能保证取到公式。
- 即使同时拼接 `m:t`，也不能恢复分数、上下标等二维结构；不能把分子 1 和分母 2 拼成 12。
- 有些公式可能以嵌入对象及预览图存在，需要结合对象关系检查。
- 图片通过文档关系 ID 对应包内文件，不能按 `image1`、`image2` 的编号猜所属题目。
- “含公式”不等于“不能使用”；简单公式可以人工核对后转写。关键是内容完整，而不是机械排除所有含公式题。

M0 可以采用“程序抽取 + 少量人工转写”，比先开发全量无损 DOCX 转换器更容易完成。不得通过删除看不懂的公式，使题目勉强通过格式校验。

### 步骤 D：挑选首批候选

先选 5–10 道用于验证整理流程，选题顺序如下：

| 优先级 | 候选特征 | 处理 |
|---|---|---|
| A | 单答案、标答明确、考点明确、内容易完整展示 | 优先整理 |
| B | 简单公式需核对转写，其他条件满足 | 转写后复核 |
| C | 多空但每空映射清楚、同一主考点、全部答案可规则判分 | 暂作备用，保留整题，明确逐空规则 |
| D | 多小问混合题、集合/作图答案、缺失内容、标答歧义 | 暂缓或待教师确认 |

遵守原规划“不拆子题”：不能把 `.001` 的 8 个小问偷偷拆成 8 条种子题，也不能只取第一个小问，却仍声称是完整的原题。

多空备用题若确需选用：

- 仍是一个 Question，以 blank_id 保存各空答案；
- 不重排小问或省略条件；
- Demo 按整题更新一次主考点，所有空判对才记整题正确；任一空待复核则整题待复核；
- 不因其有多个空就计为多个独立学习证据；
- 混合题型或多个考点强耦合的长题组仍优先暂缓。

试整理后，再争取扩展至 30–50 道、至少 5 个主考点；难度覆盖低、中、高三档，但不要为凑比例纳入无法可靠判分的题。

**若实际不足 30 道：**输出“目前可用数量、各考点数量、主要缺口和处理成本”，先保留已完成的 5–10 道流程样本。M0 暂不宣称验收通过。后续由项目范围决定是补充已审核题、调整样本目标，还是另行引入显式子题建模；本文不自动放宽既定范围。

### 步骤 E：固定最小考点映射

先复用来源中的标签，整理原标签到标准标签的映射，避免同一概念出现多个写法。

知识结构：

```text
章：有理数
→ 节：按源资料整理的教学节
→ 知识点 KP
→ 考点
↔ 题目
```

操作要求：

- 保留 section_raw，不丢失原始课时名称；标准 section 与课时不是必须一一相同。
- 来源已有 KP ID 时优先保留，不能为了好看全部换掉。
- 主属 KP、主考点与题意明显不匹配时记录问题；不能因为标签已经存在就默认它一定正确。
- 每题一个主考点，关联考点可有多个；Demo 画像只更新主考点。
- 新建考点 ID 后保持稳定，显示名称可以修正。
- 本阶段至少覆盖 5 个考点；优先控制在 8–10 个，避免过细拆分导致每点只有一题。
- 暂不把知识点强制映射为空间想象、逻辑推理等能力评分。

`rational_knowledge_points.json` 可包含 chapters、sections、knowledge_points、exam_points、source_label_mapping 五类数据。主考点和关联考点字段在种子中保存稳定 ID，名称从该文件查询。原规划的“有理数加法”等名称示例在此具体化为 ID 引用。

### 步骤 F：整理正式字段与答案规则

正式种子采用 JSON 数组，一条对象对应一个原始题目 ID。以下是**字段模板，不是已完成题目**；所有占位符必须替换后才能验收：

```json
{
  "original_qid": "<原始题目 ID>",
  "chapter": "有理数",
  "section": "<标准节名称>",
  "section_raw": "<原始课时名称>",
  "main_kp": "<KP ID>",
  "main_exam_point": "<考点 ID>",
  "related_exam_points": [],
  "question_type": "fill_blank",
  "difficulty": 1,
  "difficulty_raw": "<原始星级>",
  "question_text": "<完整题干>",
  "content_format": "plain_text",
  "options": {},
  "correct_answer": null,
  "answer_match_type": "numeric",
  "answer_normalization_rule": {
    "trim_whitespace": true,
    "normalize_fullwidth_symbols": true,
    "allow_fraction_decimal_equivalence": true,
    "allow_equivalent_fraction": true,
    "require_simplified_fraction": false,
    "require_unit": false
  },
  "solution": "",
  "wrong_analysis": "",
  "auto_gradable": false,
  "review_status": "pending",
  "source_file": "六年级_数学_校本审核/rational_有理数_校本审核（黄）.docx",
  "source_sha256": "<来源文件 SHA-256>",
  "source_locator": {
    "metadata_table_index": 1
  }
}
```

字段约定：

| 字段 | 约定 |
|---|---|
| question_type | 首批使用 choice、fill_blank；不以正文出现“选择”二字直接判断题型 |
| difficulty | 1=低，2=中，3=高；保留 difficulty_raw。可将 1–2 星映射低、3 星映射中、4–5 星映射高，作为待核对的 Demo 映射 |
| question_text | 完整、可展示的题干，不能使用 manifest 预览 |
| content_format | plain_text 或 latex；有 LaTeX 时核对页需支持对应渲染，不把裸源码当完成效果 |
| options | 选择题保存 A/B/C/D 等有序标签与完整内容，不重新排序；填空题为空对象 |
| correct_answer | 单答案使用字符串，保留符号和原有表达形式；正式 seed 不允许 null |
| solution | 只整理来源已有解析；缺失可为空，不要求模型补写 |
| wrong_analysis | 仅放已核对、适合对外展示的内容；未核对文本保留在 raw 和核对表中 |
| review_status | pending、verified、needs_teacher_review、excluded |
| auto_gradable | 仅在答案规则清楚且内容核对通过后置 true；不表示批改引擎已经实现 |
| source_file | 相对于工作区根目录；不使用仅在你电脑上有效的绝对路径 |

原始难度、题型等不一致时，先进入核对记录，不能只按表格或标题自动选值。预估得分率保留作来源信息，不当作实测正确率或学生能力分数。

判分模式的边界：

| 模式 | 适用 | M0 要明确的内容 |
|---|---|---|
| choice | 单选题 | 合法选项、唯一标答、字母大小写与空白标准化 |
| numeric | 单个有理数等简单数值答案 | 是否接受等价分数/小数、是否要求最简形式、是否要求单位 |
| exact | 指定术语或固定文字答案 | 可接受的文本集合和允许的标点/空格处理，不随意做语义猜测 |

不要把所有填空题默认设为 numeric。例如“高于/低于”需要文本规则，“写出所有满足条件的数”涉及集合及顺序规则，首批可暂缓。

若题目指定“用最简分数表示”，应同时明确禁止小数形式，并保留学生原始表达用于形式检查；只把数值约分后比较，无法发现学生写的是 2/4。

多空题是备用扩展，模板先覆盖单答案；如果纳入多空题，再明确 blank_id、各空 match_type/rule 及整题聚合方式，不用一个拼接字符串掩盖结构。

### 步骤 G：逐题核对并留记录

`rational_import_audit.csv` 建议至少包含：

```text
original_qid,status,selected,content_checked,answer_checked,
tag_checked,rule_checked,analysis_checked,issue_codes,
correction_note,reviewer,reviewed_at,confirmation_source
```

常用 issue_codes：

```text
TYPE_CONFLICT
DIFFICULTY_CONFLICT
TAG_MISMATCH
MULTI_PART
MULTI_BLANK
FORMULA_UNRESOLVED
ANSWER_TEXT_EMPTY
ANSWER_AMBIGUOUS
DRAFT_MARKER
SOURCE_CONFLICT
```

核对顺序：

1. 在 Word 中按完整 qid 找到源题。
2. 对照核对页，检查题干、选项、所有负号、分数、指数和单位。
3. 核对标答和题号/空序号；不要把“有一段答案文字”当作已确认答案。
4. 确认题型、难度、主考点、关联考点及规则。
5. 易错分析若仍是草稿，单独暂缓发布；不必因为非必需解析未完成而阻塞一条已确认的简单题。
6. 记录修正、核对人、时间和依据，再决定能否进入 seed。

状态含义：

- pending：待整理或待核对。
- verified：必需字段已与来源核对，标答和判分要求明确；有争议的内容已得到教师确认。
- needs_teacher_review：内容、标答、标签或判分标准有未解决歧义。
- excluded：不适合当前范围，保留排除原因，不代表原题有错。

个人完成的转录核对不能伪记成“老师确认”。只有实际收到的教师确认才记录在 confirmation_source 中。机器输出只能生成候选，不能自行宣告教师审核完成。

## 5. 校验脚本与核对页怎么做

### 5.1 validate_rational_seed.py

M0 校验数据合同，不要求现在实现完整批改服务。

必须检查：

- JSON 可解析，qid 非空、唯一，并能对应来源记录。
- 正式 seed 中全部为 verified 且 auto_gradable=true。
- 必填字段无 null、空值和占位符，题干完整性已人工核对。
- 题型、难度、判分模式在允许范围。
- choice 的标答是合法选项，选项标签不重复。
- fill_blank 的答案类型与规则对应；多空题的 blank_id、标答及规则一一对应。
- 主考点和关联考点存在于考点文件，主考点与 KP 关系有效。
- 来源文件存在且 SHA-256 对应本次核对版本；文件变化应提示重新核对，不能静默沿用旧结论。
- 正式种子与 audit 选用记录一致，没有未解决的必需字段问题。
- 输出总题数、题型、难度、主考点分布；少于 30 道或少于 5 个考点时完整 M0 验收不通过。

先提供两种校验模式：

- 试整理模式：核验每条数据和引用关系，允许少于 30 道；报告“结构通过、规模未达标”。
- M0 验收模式：额外检查题量、考点覆盖和三档难度覆盖，未满足时返回非零退出码。

“JSON 校验通过”不代表“题目数学正确”。自动校验和逐题核对缺一不可。

对每类答案规则附少量预期判例供 M4 使用，例如：

```text
标答 C：输入 " c " → 正确；输入 "B" → 错误
标答 1/2，允许等价：输入 "0.5"、"2/4" → 正确
要求最简分数且不允许小数：输入 "2/4"、"0.5" → 错误
无法解析的答案 → 待复核，不直接判错
```

这些是规则合同的样例，不是对现有任意题目通用的判分结论。

### 5.2 render_rational_review.py

生成本地 HTML 核对页，按 qid 展示：

- 原题 ID、来源位置、课时和原标签；
- 规范化题干、选项、标答及答案规则；
- 主考点、难度和选用状态；
- 未解决问题、修正说明和核对记录。

HTML 文本需转义，避免题干中的小于号等被当作标签；公式必须检查实际渲染效果。核对页服务于数据审核，无需先写教师后台。

## 6. 建议按三个工作批次推进

| 批次 | 做什么 | 结束时应拿到什么 |
|---|---|---|
| 第一次 | 来源统计、qid 对齐、原始块抽取，挑 5–10 道 | source_report、raw 和候选清单 |
| 第二次 | 整理 5–10 道，建立标签映射与答案规则，生成核对页 | 小样本 seed、audit、可检查的页面 |
| 第三次 | 扩展到目标数量，逐题复核，运行完整校验 | 正式 seed、考点文件、验收报告 |

不要一开始人工录完几十题后才确定字段。先用小样本确认字段、公式处理和题块边界，能避免批量返工。

脚本完成后，可约定以下命令接口；**下列命令目前是开发目标，脚本尚不存在，不能直接运行：**

```powershell
python .\code\scripts\inspect_rational_sources.py
python .\code\scripts\extract_rational_questions.py
python .\code\scripts\render_rational_review.py
python .\code\scripts\validate_rational_seed.py --mode sample
python .\code\scripts\validate_rational_seed.py --mode m0
```

## 7. M0 完成检查表

- [ ] 原始文件保持完整，来源 SHA-256 和版本信息已记录。
- [ ] DOCX 与 manifest 的 qid 已对齐，重复/缺失/冲突有记录。
- [ ] 至少 30 道、目标 30–50 道题经过核对，按原始 qid 计数。
- [ ] 至少覆盖 5 个主考点，并覆盖低、中、高三档难度。
- [ ] 每题题干、选项、标答、标签、来源和判分要求完整。
- [ ] 多问多空没有被偷拆计数；入选多空题结构和整题判分明确。
- [ ] 无未解决的公式丢失、标答歧义、题型或难度冲突。
- [ ] 待核对的易错分析未作为已确认内容发布。
- [ ] 每道正式题有真实核对记录，未把机器判断伪记为教师确认。
- [ ] 核对页已逐题对照原文，完整 M0 校验通过。
- [ ] 重跑抽取不会覆盖人工整理的种子与核对记录。
- [ ] 有明确交接说明：可用数量、覆盖范围、排除原因和后续问题。

满足以上条件后进入 M1：建立业务工程与数据库，并把这批种子数据导入。M0 的交付应让后续开发不再反复猜测“这道题的完整内容是什么、答案是什么、应该如何判分”。

## 8. 现在最先做的一件事

先实现 `inspect_rational_sources.py`，输出一份有理数来源报告和异常 qid 清单；然后从清单中挑出 5–10 道最容易完整核对的题目，跑通一轮整理。

第一轮先检验：**能否从实际 DOCX 得到一条完整、可追溯、规则清楚的题目记录。**确认这条路径可行后，再扩大数量。

