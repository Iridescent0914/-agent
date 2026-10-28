# README 运行说明 Docker 化清理记录

日期：2026-10-28

## 目标

将主 README 统一为当前真实使用的 Docker Compose 运行方式，删除容易与 Docker 环境、端口和数据库混淆的本机 conda/PostgreSQL 运行流程，并验证文档中的主要命令可以实际执行。

## README 调整

- Docker Compose 明确为唯一正式运行方式。
- 首次运行只保留镜像、容器、种子和画像初始化等必要步骤。
- 日常启动、日志、停止、源码刷新、依赖变化、迁移与种子变化、环境变量变化分别成节。
- 保留 Windows 主机 Playwright 浏览器回归作为唯一例外，并说明其仍连接 Docker 后端和数据库。
- 删除 `.conda-env`、本机 PostgreSQL、端口 `55432`、`pg_ctl`、`initdb`、`createdb` 和旧 M5 本机演示库重置入口。
- 统一命令执行目录，前端依赖安装改用 `npm ci`。
- M0 校验、资源转换、第二章题库构建以及 M2～M6 验收统一改为 Docker 命令。
- PostgreSQL 宿主机访问端口明确为 `55433`。

## Compose 调整

后端服务新增只读挂载：

```yaml
- ./scripts:/app/scripts:ro
```

该挂载使根目录下的数据构建、校验和资源转换脚本可以在后端容器的既有 Python 环境中执行，不再依赖本机 conda 环境。脚本目录只读，生成结果仍写入原有的可写 `data` 挂载。

## 陈旧测试修复

完整后端测试首次执行出现 3 个失败，原因均为测试仍假设项目只有第一章：

- 题库总数仍断言为 139，选择题仍断言为 11；当前两章数据分别为 180 和 17。
- 学生画像仍断言固定为 12 个知识点；当前接口会返回两章知识点。
- 固定时间推荐把所有章节知识点传给默认的有理数章节，触发跨章节校验。

修复后，题库验收使用当前两章基线，画像统计根据返回数据动态核对，推荐测试只选择目标章节内的知识点。

## 验证结果

- `docker compose config --quiet`：通过。
- 后端容器重新创建并成功挂载 `/app/scripts`。
- `docker compose exec -w /app backend python scripts/validate_m0.py --help`：通过。
- 后端就绪检查：`database=ok`，迁移版本 `0023`。
- `docker compose exec backend pytest -q`：`130 passed`。
- `docker compose exec frontend npm run build`：通过。
- README 中已无 `.conda-env`、`55432`、本机 PostgreSQL和旧重置脚本引用。

## 涉及文件

- `README.md`
- `docker-compose.yml`
- `backend/tests/test_api.py`
- `backend/tests/test_profile_api.py`

