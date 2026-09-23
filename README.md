# 数学教学 Agent：M0 数据准备

当前仓库已完成 Demo 第一阶段 M0，处理范围为“有理数”校本审核题库。校本审核 DOCX 是题目、标签和参考答案的权威来源，导入记录统一使用 `source_review_status=reviewed`，不要求老师再次核对已有题目。

一组小题保留为一个 `Question`，组内各空或各小题保存在 `correct_answer.items`。首批种子库只接收能按确定性规则判分的 `machine_ready` 记录。

## 当前结果

- 全量抽取：139 组题。
- M0 种子库：30 组题，其中填空题 20 组、选择题 10 组。
- 覆盖：10 个考点、4 档难度。
- 判分状态：30 组全部为 `machine_ready`，`raw_answer_only` 为 0。
- 源答案显式拆分：9 组，映射保存在 `data/config/rational_answer_structures.json`。
- 资源引用：564 个；种子题的标准答案中不含图片占位符。
- 自动验证：0 个错误。

## 运行

在 `code` 目录执行：

```powershell
python .\scripts\build_rational_m0.py
python .\scripts\validate_m0.py
```

只有需要应用 `rational_import_audit.csv` 中的开发侧技术修正时，才执行：

```powershell
python .\scripts\apply_import_corrections.py
```

脚本只使用 Python 标准库，不需要安装 `python-docx` 或 `openpyxl`。

## 目录与产物

- `data/interim/rational_all.json`：从权威 DOCX 抽取的 139 组全量中间数据。
- `data/seed/rational_questions.json`：M1 可以直接导入的 30 组确定性判分种子题。
- `data/seed/rational_knowledge_tree.json`：章—知识点—考点—题目的首版知识树。
- `data/config/rational_answer_structures.json`：由校本原始答案直接拆出的逐空答案映射。
- `data/audit/rational_import_audit.csv`：开发者导入检查与技术修正表。
- `data/audit/rational_m0_report.json`：抽取、筛选、资源和问题统计。
- `data/assets/rational/`：从 DOCX 导出的公式和题目图片。
- `scripts/build_rational_m0.py`：抽取、结构化、资源导出和种子筛选。
- `scripts/validate_m0.py`：M0 验收校验。
- `scripts/apply_import_corrections.py`：可选技术修正应用脚本。
- `docs/04_M0_第一步工作总结与M1交接.md`：M0 结果与 M1 开工边界。

## 全量中间库与种子库

`data/interim/rational_all.json` 是有理数章节的全量中间库，共 139 组。所有成功抽取的题都会进入该文件，是否进入不取决于 `raw_answer_only`。

- `machine_ready`：答案已经能够由程序确定性判分；全量库中有 41 组。
- `raw_answer_only`：原始参考答案已保存，但尚未形成可靠的逐空或逐小题判分结构；全量库中有 98 组。
- `data/seed/rational_questions.json`：从 `machine_ready` 记录中筛出的 30 组当前种子题。

98 组 `raw_answer_only` 还包括当前范围外的计算题和解答题，并非都要立即结构化。

## 尚未进入种子库的题怎么处理

全量中间库中的 `raw_answer_only` 记录不会丢失，也不会直接用于 M1 组卷。它们按以下流程晋级：

1. 在导入检查表中定位 `answer_items_not_structured`、资源缺失或题型不一致等技术问题。
2. 对纯文本参考答案，在 `rational_answer_structures.json` 中补充 `item_id`、答案和匹配方式。
3. 对图片或公式答案，先把 WMF 转成浏览器可显示格式或转写为可比较表达式。
4. 重新运行构建和验证；只有 `machine_ready` 且通过全部校验的记录才进入种子库。

这属于开发侧数据结构化工作。若校本原文确实缺题干、缺答案或自身矛盾，才单独列为内容问题交给老师处理。

## M1 使用约束

当前只开发有理数章节，不抽象多章节通用导入流水线，也不处理其他章节数据。M1 只导入 `data/seed/rational_questions.json` 与 `data/seed/rational_knowledge_tree.json`，先跑通有理数题库、学生、组卷和批改闭环。

题库页面若要展示含公式图片的题目，需要先处理 WMF 的 Web 格式转换；这不影响 M0 的答案判分验收，但属于 M1 前端展示的开工任务。
