# S3-D：百炼视觉HTTP与费用控制接入记录

日期：2026-11-19。承接[逐页模拟任务](04_S3逐页视觉协议与模拟任务记录.md)。

本轮已把百炼视觉HTTP接入新版逐页任务，提供独立配置、费用预占与真实模式页面。完成的是代码及离线验证；未使用真实Key、未发送实际学生照片、未产生本轮API费用，也未完成手写质量验收或S3-E。

## 1. 范围及生效边界

- 沿用一份学生版试卷：题目在前，另页答题区在后。仍从原试卷详情上传答题照片，不新增独立模型试验页面。
- S3-C收集照片，S3-D按有效照片逐页提取；补拍只使对应页结果失效，未变页复用当前相同配置的任务。
- 视觉只读取实际书写，不接收标准答案、题干、解析或学生档案，不计算、不纠正答案；纸面短码/产物编号/页码由模型独立观察，不能在请求中提供期望值让其回显。
- 真实返回也保持`needs_review`、`can_submit=false`，结果中`synthetic=false`仅表示来自真实供应商链路，不代表识别正确或获准采用。正式整卷抄录确认和成绩提交仍待S3-E及后续闭环。
- 按卷记录提取错误、质量接受阈值、低正确率异常和0.6系数均按最新需求暂缓，没有增加相关字段或运行策略。

## 2. 独立配置与启用

新增配置以`STAGE6_VISION_*`为前缀；不读取旧`VISION_*`、文本等价判断或阶段四导入的Key/地址。

| 配置 | 默认值 | 含义 |
| --- | --- | --- |
| `STAGE6_VISION_PROVIDER` | `fake` | `fake`离线模拟；`bailian`真实HTTP；其他值阻断 |
| `STAGE6_VISION_API_KEY` | 空 | SecretStr，本地保管，不写入任务、响应或日志 |
| `STAGE6_VISION_BASE_URL` | 空 | 北京地域匹配业务空间的兼容API根路径 |
| `STAGE6_VISION_MODEL` | `qwen3.8-flash` | 首个候选，可按允许列表切换对照模型 |
| `STAGE6_VISION_TIMEOUT_SECONDS` | 45 | 单次调用秒数，上限120 |
| `STAGE6_VISION_LEASE_SECONDS` | 120 | 必须大于超时，避免正常调用期间被重复领取 |
| `STAGE6_VISION_MAX_OUTPUT_TOKENS` | 8192 | 512～16384，截断结果不能作为完整抄录 |
| `STAGE6_VISION_MAX_PIXELS` | 4194304 | 262144～16777216，外发图像的像素上限 |
| `STAGE6_VISION_MAX_CALLS` | 30 | 本数据库累计真实调用次数上限，重试/重跑另计 |
| `STAGE6_VISION_MAX_BUDGET_CNY` | 2 | 本数据库累计预估及预占费用限制，不按月重置 |

继续要求`STAGE6_ENABLED=true`及`APP_ENV=development/test`。真实配置不全时返回配置错误，不自动降级到fake。Key可以与同地域、同业务空间下有权限的其他任务使用同一把，但必须在本组配置显式填写；不自动复制读取其他任务Key。

本轮已向本地`.env`追加缺少的10项占位和默认值，既有配置值未覆盖，Key及地址留空，供应商仍为fake。`.env.example`、Compose和后端Settings已同步，不向Git提交本地`.env`。

准备真实联调时，在本地填写：

```dotenv
STAGE6_ENABLED=true
STAGE6_VISION_PROVIDER=bailian
STAGE6_VISION_API_KEY=<本地Key>
STAGE6_VISION_BASE_URL=https://<真实WorkspaceId>.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
STAGE6_VISION_MODEL=qwen3.8-flash
```

占位符必须换成真实值，Base URL不要追加`/chat/completions`。支持北京业务空间域名及既有`https://dashscope.aliyuncs.com/compatible-mode/v1`地址；使用哪一个取决于账号控制台给出的配置。拒绝HTTP、其他地域、任意外部主机、URL用户密码、查询参数及片段。当前报价策略仅覆盖北京地域，不将北京单价套用于境外调用。

环境变量修改后在仓库根目录运行：

```powershell
docker compose up -d --force-recreate backend
```

仅`docker compose restart backend`不会重新读取Compose环境变量。没有新增依赖或数据库迁移；已有0036即可。本轮没有重新创建业务backend来启用真实模式，也没有改写业务成绩。

## 3. HTTP与图像边界

新增`services/vision_bailian.py`，通过`httpx`请求`POST {BaseURL}/chat/completions`。采用Base64内联JPEG，不要求上传照片到公开图床，不传服务器本地文件路径。

请求仅包含：图片、抄录提示、`PageVisionRequest`白名单中的无业务含义令牌、题号/空号、作答框坐标、页尺寸和选择空允许字母。未知请求字段直接拒绝，不把数据库snapshot整体发出。

调用前要求核对人确认当前有效照片已遮挡姓名、学生编号等个人信息，后端对应`external_images_reviewed=true`才允许创建真实任务；该确认写入不可变任务快照并绑定当前图片。模板顶部印有学生编号，脱敏时应遮挡该处，保留短码、版本和底部产物/答题页标记用于核对。系统不会自动识别或遮挡这些像素，不能把勾选本身称为已自动脱敏。补拍/切换照片后页面清除本次勾选，新任务需重新核对。

图片外发前核验原图摘要，重新编码为RGB JPEG并清除元数据；大图按像素上限等比缩小。另向供应商显式设置`max_pixels`及关闭固定高分辨率模式。此处理不做透视校正、自动裁剪或自动隐私遮挡，模糊原图也不会因缩放变清晰。

首批允许的模型及计价依据：

| 模型 | 输入/输出，元/百万Token | 响应模式 |
| --- | --- | --- |
| `qwen3.8-flash` | 0.8 / 2.7 | JSON Schema |
| `qwen3.7-flash`及`qwen3.7-flash-2026-07-15` | 0.2 / 0.8 | JSON Schema |
| `qwen3.7-plus`及`qwen3.7-plus-2026-05-26` | 2 / 8 | JSON Schema |
| `qwen3-vl-plus`及`qwen3-vl-plus-2025-12-19` | 1 / 10 | JSON Object加后端严格校验 |

单价按2026-11-19核对的北京实时原价、输入不超过32K阶梯，不计缓存和活动优惠。所有请求的文本/Schema字节估计、图片Token估计及安全余量合计超过32,000时，在调用前拒绝，避免按低阶梯预占大输入。Token估计不是供应商账单；供应商报告超出预占依据时保留已知费用并暂停后续调用。实际用量金额计算包含相应模型更高输入阶梯处理，不能把超界用量继续当低阶梯收费。

思考关闭、非流式、不使用工具。HTTP不自动跟随跳转，不读取环境代理，不自动网络重试。网络/超时及401/403/404/429等分开记录错误码，不保留供应商任意错误正文或异常文本。响应流限制1MiB，保存内容限制262144字符；只保留允许的完成文本和Token计数，不留思考内容、任意供应商回显或Base64输入副本。Key若在完成文本中被回显会脱敏。

后端继续严格校验JSON重复键、响应/请求令牌、完整且不重复的槽位集合、选择字母、状态/文本关系及纸面身份。合法JSON不等于合法抄录，HTTP200也不等于结果可提交。截断、身份不符、非法结构均不进入正式答案。

## 4. 持久化、复用与费用控制

复用0036的`VisionPageTask`和`VisionPageCall`：

- 真实供应商的完整非敏感配置写入不可变snapshot：地址、模型、提示版本、像素/输出限制、单价/版本、超时等。不保存Key。费用上限本身采用当前设置，可在不改变抄录内容的情况下收紧。
- 配置变化后旧任务失效，不能把旧模型结果当作新模型结果；相同图片、配置和有效页绑定才复用。fake任务不升级为付费任务，需显式建立当前真实配置的新任务。
- 费用门禁使用独立数据库事务级advisory lock，在任何HTTP前保存调用记录和最大输出/保守输入费用预占，提交后才发请求。多个worker或学生共享本库累计额度，不各自从零计数。
- 实际Token数有效时计算预估费用并释放差额；格式/页身份错误仍保留已知调用费用，不能因为结果不可用就当免费。
- HTTP错误、超时、断网、无法读取用量都按用量未知处理，不假定失败免费，阻断后续真实调用；已到次数/金额上限则在请求前拒绝，不新增一次付费尝试。
- 取消进行中的任务不自动退款。过期租约不会盲目重发真实请求；旧调用先视为未知，后续迟到响应仍可补记原调用的已知用量，但不能覆盖新任务/教师操作。
- 当前没有“一键清空费用”或“忽略未知调用继续”的入口。未知用量须先核对百炼记录，后续如需补记，另行实现可审计的用量修正流程；不能通过换Key、重跑或重启绕过限制。

这套门禁控制应用发起请求的预估开销，不是阿里账单的硬扣费上限，也不覆盖阶段四、旧视觉入口或文本等价判断的其他费用。

## 5. 页面与操作

原照片页面增加真实模式：展示已配置模型、累计调用/预估费用/预占/上限，配置错误和用量未知会明确提示。用户核对照片、核对人及原因后，可建立真实逐页任务，处理后手动刷新查看结果和调用记录。

默认fake继续显示原“运行模拟提取”；真实模式显示“运行真实提取”，任务详情区分模拟和真实，并显示预估费用或“费用待核对”。识别结果尚不提供正式提交按钮，不自动触发文本等价判断、Attempt、判分修订或画像。

## 6. 验证与剩余事项

- 后端定向回归312项通过，包含视觉MockTransport、请求白名单、独立配置、脱敏核对门禁、像素/元数据、HTTP状态、预算预占及另一个数据库连接可见性、用量未知、迟到/取消、fake切换真实/模型切换、快照不可变，以及S1/S2/S3、打印、判分和累计画像回归。
- 桌面和390px手机照片页面16项通过，包含真实按钮核对门禁、费用展示、补拍后重置核对、配置错误不降级、未知用量阻断及原模拟/照片管理流程。截图仅使用API模拟与合成图片。
- 类型检查及Vite生产构建通过，保留既有MathLive大chunk提示。`git diff --check`通过，相关Markdown链接已检查，未发现失效本地目标。
- 隔离库为`math_agent_stage6_s1_test`，测试进程禁用阶段四、旧视觉与文本真实调用，视觉Key清空，测试中注入假Key及MockTransport。另连接预算测试使用隔离临时schema并在完成后清理；业务库不参与测试。

后端复现命令（仓库根目录）：

```powershell
docker compose run --rm --no-deps `
  -e DATABASE_URL=postgresql+psycopg://math_agent:math_agent_dev@postgres:5432/math_agent_stage6_s1_test `
  -e APP_ENV=test -e QUESTION_IMPORT_S2_ENABLED=false -e QUESTION_IMPORT_S2_API_KEY= `
  -e VISION_ENABLED=false -e VISION_PROVIDER=fake -e VISION_API_KEY= `
  -e STAGE6_EQUIVALENCE_PROVIDER=fake -e STAGE6_EQUIVALENCE_API_KEY= `
  -e STAGE6_VISION_PROVIDER=fake -e STAGE6_VISION_API_KEY= `
  backend python -m pytest -q tests/test_stage6_vision_bailian.py tests/test_stage6_s3_vision_pages.py tests/test_stage6_s3_pages.py tests/test_stage6_s3_artifacts.py tests/test_stage6_s3_preparation.py tests/test_stage6_s1.py tests/test_stage6_s2.py tests/test_stage6_bailian.py tests/test_grading.py tests/test_print_layout.py tests/test_profile_historical_accuracy.py
```

前端在`frontend`目录执行：

```powershell
$env:PLAYWRIGHT_PORT='5187'
npm.cmd run test:e2e -- tests/answer-pages.spec.ts
npm.cmd run build
```

下一步由本地填写真实配置后进行小额手写照片联调，核对模型权限、响应结构、Token用量、负号/分数/涂改和槽位对应；之后实现S3-E整卷合并与人工抄录修订。离线测试通过不表示这些真实质量已验收。当前无新增迁移，无本轮提交/push。

官方接口和单价依据：[图像与视频理解](https://help.aliyun.com/zh/model-studio/vision)、[结构化输出](https://help.aliyun.com/zh/model-studio/qwen-structured-output)、[模型价格](https://help.aliyun.com/zh/model-studio/model-pricing)。
