# 数学教学 Agent：M0 数据准备

本目录保存 Demo 的代码与处理后数据。M0 仅处理“有理数”校本审核题库，目标是生成可追溯的结构化题目、首批候选题和导入检查表。原题中的一组小题保留为一个 Question，组内答案保存在 `correct_answer.items`，不拆成多个题库实体。

“校本审核 DOCX”是题目、标签和参考答案的权威来源，导入后统一记录 `source_review_status=reviewed`，不要求老师再次审核数学内容。原始 DOCX 保留在项目根目录；生成数据记录相对源路径和原始题目 ID。

## 运行

```powershell
python .\scripts\build_rational_m0.py
python .\scripts\validate_m0.py
# 只有开发者修正导入结果后才需要：
python .\scripts\apply_import_corrections.py
```

脚本只使用 Python 标准库，不需要安装 `python-docx` 或 `openpyxl`。

## 输出

- `data/interim/rational_all.json`：从权威 DOCX 抽取的全部题目。
- `data/seed/rational_questions.json`：满足首批规则的 30–50 组候选题。
- `data/seed/rational_knowledge_tree.json`：首版章—知识点—考点树。
- `data/audit/rational_import_audit.csv`：开发者使用的导入检查与修正表。
- `data/audit/rational_m0_report.json`：抽取、筛选与问题统计。
- `data/assets/rational/`：从 DOCX 导出的公式预览图和题目图片。
- `data/seed/rational_questions_corrected.json`：应用可选技术修正后生成，不覆盖初始种子数据。

首批候选限定为题量最多的 10 个考点，并允许小题组。`grading_readiness=machine_ready` 表示答案已成功拆成逐项可判分结构；`raw_answer_only` 表示标准答案已经从权威 DOCX 保存，但代码尚未可靠拆成逐空答案。后者是开发任务，不是教师审核任务。

`import_status=needs_technical_cleanup` 只表示存在题型标签不一致、答案尚未逐项结构化或资源格式需要转换等技术问题。部分公式资源是 WMF，进入前端开发前需要转换为浏览器可显示格式。
