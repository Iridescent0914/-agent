# KP—主属节映射修复记录

日期：2026-11-10

## 问题与来源

《上海市五四制初中数学知识图谱.html》的 `SECTION_BY_KP` 按章节内 KP 顺序明确给出主属节，是节—KP 对应的**唯一依据**。原来 `data/seed/rational_knowledge_tree.json` 的 13 个 KP 和 `data/seed/expr_knowledge_tree.json` 的 8 个 KP 都没有 `section` 字段。种子导入器读取缺失字段时会将 `KnowledgePoint.section` 写成 `NULL`；修复前，本地数据库这 21 个 KP 的节字段确实全部为空。

按 HTML 映射、两份知识树的 KP 名称及顺序、`data/interim` 中 180 道现有题的节字段交叉核对，21 个 KP 的映射数量和内容均一致。特别是“有理数的运算律及其简化运算的作用”属于 `1.2 有理数的加法与减法`，按 HTML 原映射写入，不按 KP code 或题意另行猜节。

## 修复内容

1. 两份种子知识树逐个 KP 增加正式节名，共 21 条。知识树生成脚本和种子导入器直接读取 HTML 映射，校验章节、KP 名称及顺序。知识树若与 HTML 不一致则停止导入；题目原始节名若不一致，入库时按其主属 KP 在 HTML 中的节名归一。
2. 增加 Alembic `0026_backfill_pilot_kp_sections.py`：对**已有数据库**中这 21 个 KP 的节字段按 HTML 映射写入，空值和非空冲突值均覆盖。数据库尚未导入的 KP 不由迁移创建，后续正常种子导入会写入节名。
3. 题库目录与画像目录优先使用 `KnowledgePoint.section`；只有没有正式节名的旧 KP 才回退到现有题目的节字段。题库目录的章节节列表也从正式 KP 汇总，使尚无题目的正式 KP 所属节可以显示。
4. 修正 S0 标签快照生成脚本在文档目录移动后的仓库路径，并增加“知识树节名必须与 HTML 一致”的断言；重新生成标签目录快照。阶段四计划与 S0 契约同步更新。迁移只修改 `knowledge_points.section`，不重导题目，也不修改题干、答案、作业或历史快照。

## 验证结果

- 本地数据库迁移从 `0025` 升级到 `0026 (head)`，升级后按 code 与两份知识树逐条对比：**21/21 一致，0 条缺失或冲突**。
- `pytest -q tests/test_knowledge_point_sections.py tests/test_seed_contract.py tests/test_catalog_management.py tests/test_profile_api.py`：**23 passed**。测试覆盖 HTML、知识树、迁移常量和 180 道现有题的一致性；题目节名归一、知识树偏离 HTML 时停止导入、迁移覆盖旧节名，以及目录和画像接口。错误题目节的目录测试只在事务内临时修改数据并回滚。
- `pytest -q tests/test_catalog_management.py tests/test_profile_api.py`：**14 passed**，现有题库目录/编辑和画像 API 回归通过。
- S0 生成脚本通过 HTML、知识树和 180 道题交叉校验，生成 2 章、21 KP 的标签快照；PDF 评测页级清单仍为 7 文件、209 页。
- `git diff --check` 通过。

## 部署与边界

已有环境执行 `alembic upgrade head` 即可修复现存 KP；仓库的 Compose 后端启动命令也会执行迁移。镜像和 Compose 都已提供原始 HTML 文件，更新后端需要重建镜像或重建容器。新环境在迁移后运行 `python -m scripts.seed_database --chapter all`，由已补字段的知识树创建完整 KP。无需为了本次修复在运行中的环境重导 180 道题。

`0026` 的 downgrade 不清除节名，因为无法可靠区分迁移补入与之后正常种子导入或人工维护的值。当前修复仅覆盖已有正式种子树的前两章；其他章节进入题库前，需按 HTML 的 `SECTION_BY_KP` 扩展其正式知识树和数据库目录。

## 协作同步

本地数据库已升级至 `0026`，前两章 21/21 个 KP 与 HTML 一致。**合作者的工作区和数据库没有自动更新**：目前这些改动仍在本地，尚未提交或推送。代码进入共享仓库后，合作者拉取改动，在 `code` 目录运行 `docker compose up -d --build backend`；后端启动命令会执行 `alembic upgrade head`。若不使用 Compose，则在其后端环境手动执行迁移。已记录为 `0026` 的数据库不会因为迁移脚本后续变化再次执行 `0026`；若该环境已有 `0026` 且存有冲突节名，需要另行校正，不能只重启服务。
