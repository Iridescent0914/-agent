# 数学教学 Agent：M0 数据准备

本目录保存 Demo 的代码与处理后数据。M0 仅处理“有理数”校本审核题库，目标是生成可追溯的结构化题目、首批候选题和人工核对表。原题中的一组小题保留为一个 Question，组内答案保存在 `correct_answer.items`，不拆成多个题库实体。

原始 DOCX 保留在项目根目录的资料文件夹中，不在此目录复制；生成数据会记录相对源路径和原始题目 ID。

## 运行

```powershell
python .\scripts\build_rational_m0.py
python .\scripts\validate_m0.py
# 教师填写核对表后：
python .\scripts\apply_teacher_review.py
```

两个脚本只使用 Python 标准库，不需要安装 `python-docx` 或 `openpyxl`。

## 输出

- `data/interim/rational_all.json`：从权威 DOCX 抽取的全部题目。
- `data/seed/rational_questions.json`：满足首批规则的 30–50 道候选题。
- `data/seed/rational_knowledge_tree.json`：首版章—知识点—考点树。
- `data/audit/rational_import_audit.csv`：供教师逐题确认的核对表。
- `data/audit/rational_m0_report.json`：抽取、筛选与问题统计。
- `data/assets/rational/`：从 DOCX 导出的公式预览图和题目图片。
- `data/seed/rational_questions_reviewed.json`：应用教师核对结果后生成，不覆盖初始种子数据。

`verified` 只能由教师在核对表中确认。程序生成的候选题默认是 `pending` 或 `needs_teacher_review`。

首批候选限定为题量最多的 10 个考点，当前允许小题组。`grading_readiness=machine_ready` 表示答案已成功拆成可判分项；`needs_teacher_review` 表示教师需要在核对表中补充或修正组内答案结构。部分公式资源是 WMF，进入前端开发前需要转换为浏览器可显示格式。

教师在 CSV 中把 `audit_status` 改为 `verified`，必要时在 `teacher_final_answer` 填写 JSON 答案结构，再运行 `apply_teacher_review.py`。重新运行构建脚本会重建核对表，因此应先应用或备份已填写的 CSV。
