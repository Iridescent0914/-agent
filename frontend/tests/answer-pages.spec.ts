import { expect, test, type Page } from '@playwright/test'
import type { PageVisionTask } from '../src/types/visionPages'

const paperId = '00000000-0000-4000-8000-000000000091'
const artifactId = '00000000-0000-4000-8000-000000000092'
const artifactCode = 'A123456789ABCDEF'
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aHZkAAAAASUVORK5CYII=', 'base64')
const prefix = `/api/v1/internal/stage6/paper-instances/${paperId}/answer-card-artifacts`

async function mockWorkspace(page: Page, { failSecond = false, archived = false, noArtifacts = false, vision = false, realVision = false, usageUnknown = false, visionMisconfigured = false, transcription = false } = {}) {
  const images: Array<{ id: string; available: boolean; image_url: string }> = []
  const history: Array<{ revision_no: number; page_no: number; action: string; before_image_id: string | null; after_image_id: string | null; reviewer: string; reason: string }> = []
  const selected: Record<number, string | null> = {}
  let exists = false, revision = 0, calls = 0
  let failure = failSecond
  const unexpected: string[] = []
  const visionTasks: PageVisionTask[] = []
  let visionPosts = 0
  let draft: any = null
  let submittedCount = 0
  const transcriptionView = () => draft ? { ...draft, stale: !draft.submission_id && draft.collection_revision !== revision,
    stale_message: '照片集合已改变，请重新合并并核对' } : { id: null, revision_no: 0, can_submit: false, units: [], history: [] }
  const visionView = () => {
    const rows = visionTasks.map(t => ({ ...t,
      is_current: selected[t.page_no] === t.image_id && !visionTasks.some(newer => newer.page_no === t.page_no && newer.generation > t.generation && newer.image_id === t.image_id),
      effective_status: selected[t.page_no] === t.image_id ? t.status : 'stale',
    }))
    return { configuration: { provider: realVision ? 'bailian' : 'fake', model: realVision ? 'qwen3.8-flash' : 'fake-stage6-vision-page-v2', synthetic: !realVision,
      error_code: visionMisconfigured ? 'VISION_CONFIG_INCOMPLETE' : null, real_provider_available: realVision && !visionMisconfigured },
      budget: { scope: 'database_cumulative_page_vision', calls: visionPosts * 2, unknown_calls: usageUnknown ? 1 : 0,
        estimated_cost_cny: visionPosts * .02, reserved_cost_cny: 0, max_calls: 30, max_budget_cny: 2 },
      collection_id: 'collection-test', collection_revision: revision,
      current_tasks: rows.filter(t => t.is_current).sort((a, b) => a.page_no - b.page_no), history: rows, can_submit: false }
  }
  const newVisionTask = (number: number, generation = 1) => {
    const task: PageVisionTask = { id: `vision-${visionTasks.length + 1}`, page_no: number,
      image_id: selected[number]!, generation, revision_no: 3, status: 'needs_review', effective_status: 'needs_review',
      is_current: true, synthetic: !realVision, provider: realVision ? 'bailian' : 'fake', model: realVision ? 'qwen3.8-flash' : 'fake-stage6-vision-page-v2',
      attempt_count: 1, max_attempts: 3, error_code: null, binding_error: null, received_unit_count: 1, can_submit: false,
      identity_check: { status: 'unconfirmed', unknown_fields: ['artifact_code'], mismatched_fields: [] } }
    visionTasks.unshift(task)
    return task
  }
  const view = () => ({
    id: 'collection-test', artifact_id: artifactId, artifact_code: artifactCode, revision_no: revision, total_pages: 2,
    status: selected[1] && selected[2] ? 'collected' : 'collecting',
    missing_page_nos: [1, 2].filter(n => !selected[n]), collection_ready: Boolean(selected[1] && selected[2]),
    is_active: !archived, recognition_status: 'not_started', can_submit: false,
    effective_input_sha256: String(revision),
    pages: [1, 2].map(n => ({ page_no: n, effective_image_id: selected[n] ?? null, status: selected[n] ? 'uploaded' : 'missing' })),
    images, history: [...history].reverse(),
  })
  await page.route('**/api/v1/**', async route => {
    const request = route.request()
    const url = new URL(request.url()).pathname
    if (url.endsWith('/internal/stage6/status')) return route.fulfill({ json: { enabled: true } })
    if (url.endsWith('/submission')) return route.fulfill({ json: { student_code: 'S001', instance_code: 'PAPER-001', version_no: 1, is_active: !archived } })
    if (url === prefix) return route.fulfill({ json: noArtifacts ? [] : [{ id: artifactId, artifact_code: artifactCode, revision_no: 1, total_pages: 2 }] })
    if (url === `${prefix}/${artifactId}/page-collection`) {
      if (request.method() === 'POST') exists = true
      return route.fulfill({ json: exists ? view() : null })
    }
    if (/\/page-collection\/images\//.test(url)) return route.fulfill({ contentType: 'image/png', body: png })
    if (url.endsWith('/page-collection/transcription') && request.method() === 'GET') return route.fulfill({ json: transcriptionView() })
    if (url.endsWith('/page-collection/recognition-review') && request.method() === 'GET') return route.fulfill({ status: 503, json: { message: 'quality review unavailable in transcription fixture' } })
    if (url.endsWith('/transcription/merge') && transcription) {
      const body = request.postDataJSON()
      expect(body.expected_revision_no).toBe(draft?.revision_no ?? 0)
      expect(body.expected_collection_revision).toBe(revision)
      draft = { id: 'draft-test', revision_no: (draft?.revision_no ?? 0) + 1, collection_revision: revision,
        input_sha256: 'a'.repeat(64), can_submit: false, confirmed_page_nos: [],
        history: [{ revision_no: 1, action: 'merge', reviewer: 'lin', reason: '合并整卷' }],
        pages: [1, 2].map(n => ({ page_no: n, synthetic: !realVision, image_url: `${prefix}/${artifactId}/page-collection/images/${selected[n]}`,
          identity_check: { status: 'unconfirmed' } })),
        units: [1, 2].map(n => ({ slot_key: `item:sub_${n}.blank_1`, order_no: 3, subquestion_no: n, blank_no: 1,
          page_no: n, source_module: n === 1 ? 'required' : 'history', original_status: 'readable', original_answer: n === 1 ? '-156' : '30',
          recognition_status: 'readable', answer: n === 1 ? '-156' : '30', confidence: .8 })) }
      return route.fulfill({ json: transcriptionView() })
    }
    if (url.endsWith('/page-collection/transcription') && request.method() === 'PUT' && transcription) {
      const body = request.postDataJSON()
      expect(body.expected_revision_no).toBe(draft.revision_no)
      expect(body.expected_input_sha256).toBe(draft.input_sha256)
      draft.units = draft.units.map((u: any) => ({ ...u, ...body.units.find((v: any) => v.slot_key === u.slot_key) }))
      draft.confirmed_page_nos = body.confirmed_page_nos
      draft.revision_no++
      draft.can_submit = realVision && body.confirmed_page_nos.length === 2
      return route.fulfill({ json: transcriptionView() })
    }
    if (url.endsWith('/transcription/confirm') && transcription) {
      const body = request.postDataJSON()
      expect(body.expected_revision_no).toBe(draft.revision_no)
      expect(body.transcription_confirmed).toBe(true)
      expect(draft.units[0].original_answer).toBe('-156')
      submittedCount++; draft.submission_id = 'submission-test'; draft.can_submit = false
      return route.fulfill({ json: { submission_id: draft.submission_id } })
    }
    if (url.endsWith('/page-collection/vision-tasks')) {
      if (request.method() === 'POST') {
        visionPosts++
        if (!vision && !realVision) return route.fulfill({ status: 500, json: { message: 'unexpected vision POST' } })
        if (realVision) expect(request.postDataJSON().external_images_reviewed).toBe(true)
        for (const n of [1, 2]) {
          if (selected[n] && !visionTasks.some(t => t.page_no === n && t.image_id === selected[n])) newVisionTask(n)
        }
      }
      return route.fulfill({ json: visionView() })
    }
    const visionAction = url.match(/\/vision-tasks\/(vision-\d+)\/(retry|cancel|rerun)$/)
    if (visionAction) {
      const task = visionTasks.find(t => t.id === visionAction[1])!
      if (visionAction[2] === 'rerun') newVisionTask(task.page_no, task.generation + 1)
      return route.fulfill({ json: {} })
    }
    const visionDetail = url.match(/\/vision-tasks\/(vision-\d+)$/)
    if (visionDetail) {
      const task = visionTasks.find(t => t.id === visionDetail[1])!
      return route.fulfill({ json: { ...task, snapshot: { slots: [{ slot_token: 'slot', order_no: 3, subquestion_no: 1, blank_no: 1 }] },
        result: { answers: [{ slot_token: 'slot', recognition_status: 'illegible', answer: null, confidence: null }] },
        calls: [{ id: 'call-test', call_no: 1, status: 'succeeded', raw_response: '{"content":"offline mock"}', usage: { cost_cny: realVision ? .0102 : 0 }, late_response: false }],
        action_history: [{ action: 'create', reviewer: 'lin', reason: '逐页模拟', at: '2026-11-19' }] } })
    }
    const upload = url.match(/\/page-collection\/pages\/(\d+)\/images$/)
    if (upload) {
      calls++
      if (calls === 2 && failure) { failure = false; return route.fulfill({ status: 409, json: { message: '照片集合已改变，请刷新后操作' } }) }
      const number = Number(upload[1]), id = `image-${calls}`
      const before = selected[number] ?? null
      images.push({ id, available: true, image_url: `${prefix}/${artifactId}/page-collection/images/${id}` })
      selected[number] = id; revision++
      history.push({ revision_no: revision, page_no: number, action: 'upload', before_image_id: before, after_image_id: id, reviewer: 'lin', reason: '核对纸面页码' })
      return route.fulfill({ json: view() })
    }
    const selection = url.match(/\/page-collection\/pages\/(\d+)\/effective-image$/)
    if (selection) {
      const number = Number(selection[1]), body = request.postDataJSON() as { image_id: string | null }
      const before = selected[number] ?? null
      selected[number] = body.image_id; revision++
      history.push({ revision_no: revision, page_no: number, action: body.image_id ? 'select' : 'remove', before_image_id: before, after_image_id: body.image_id, reviewer: 'lin', reason: '核对纸面页码' })
      return route.fulfill({ json: view() })
    }
    unexpected.push(`${request.method()} ${url}`)
    return route.fulfill({ status: 500, json: { message: 'unexpected API' } })
  })
  return { unexpected, uploads: () => calls, visionPosts: () => visionPosts, submittedCount: () => submittedCount }
}

async function openCollection(page: Page) {
  await page.goto(`/paper-instances/${paperId}/recognize`)
  await page.getByRole('combobox', { name: '纸面产物编号' }).click()
  await page.getByText(`${artifactCode} · r1 · 2 个答题页`, { exact: true }).click()
  await page.getByRole('checkbox', { name: '已对照纸面核对学生、试卷短码、版本和产物编号' }).check()
  await page.getByRole('button', { name: '建立照片集合' }).click()
  await expect(page.getByText('待补拍：答题页 1、2', { exact: true })).toBeVisible()
  await page.getByRole('textbox', { name: '核对人', exact: true }).fill('lin')
}

async function queue(page: Page, names: string[]) {
  await page.getByLabel('选择多页照片', { exact: true }).setInputFiles(names.map(name => ({ name, mimeType: 'image/png', buffer: png })))
  await page.getByRole('checkbox', { name: '已逐张对照照片核对产物编号及答题页编号' }).check()
  await page.getByRole('button', { name: /上传所选照片/ }).click()
}

test('collect photos, retake, restore history, remove attribution and persist across reload', async ({ page }, testInfo) => {
  const mock = await mockWorkspace(page)
  await openCollection(page)
  await queue(page, ['page-1.png', 'page-2.png'])
  await expect(page.getByText('答题页照片已收齐，请查看下方提取任务', { exact: true })).toBeVisible()
  expect(mock.uploads()).toBe(2)
  await page.screenshot({ path: testInfo.outputPath('answer-pages-collected.png'), fullPage: true })
  await queue(page, ['retake.png'])
  await expect(page.getByRole('img', { name: '答题页 1 历史照片 2', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '设为本页有效照片', exact: true }).click()
  await expect(page.getByText(/v4 · 答题页 1 · 切换有效照片/)).toBeVisible()
  await page.getByRole('button', { name: '移除本页归属', exact: true }).first().click()
  await expect(page.getByText('待补拍：答题页 1', { exact: true })).toBeVisible()
  await page.reload()
  await page.getByRole('combobox', { name: '纸面产物编号' }).click()
  await page.getByText(`${artifactCode} · r1 · 2 个答题页`, { exact: true }).click()
  await expect(page.getByText('待补拍：答题页 1', { exact: true })).toBeVisible()
  expect(mock.unexpected).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy()
})

test('partial upload failure retains successful page and retries only pending photo', async ({ page }) => {
  const mock = await mockWorkspace(page, { failSecond: true })
  await openCollection(page)
  await queue(page, ['first.png', 'second.png'])
  await expect(page.getByText('照片集合已改变，请刷新后操作', { exact: true })).toBeVisible()
  await expect(page.getByText('待补拍：答题页 2', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '上传所选照片（1 张）', exact: true })).toBeEnabled()
  await page.getByRole('button', { name: '刷新照片集合', exact: true }).click()
  await expect(page.getByRole('button', { name: '上传所选照片（1 张）', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '上传所选照片（1 张）', exact: true }).click()
  await expect(page.getByText('答题页照片已收齐，请查看下方提取任务', { exact: true })).toBeVisible()
  expect(mock.uploads()).toBe(3)
  expect(mock.unexpected).toEqual([])
})

test('no artifact directs teacher to the existing combined student-paper print flow', async ({ page }) => {
  const mock = await mockWorkspace(page, { noArtifacts: true })
  await page.goto(`/paper-instances/${paperId}/recognize`)
  await expect(page.getByText('尚无固定分页的答题页记录', { exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: '打开学生试卷' })).toHaveAttribute('href', `/paper-instances/${paperId}/print`)
  await expect(page.getByRole('button', { name: /开始提取|提交为待复核/ })).toHaveCount(0)
  expect(mock.unexpected).toEqual([])
})

test('duplicate page assignments are rejected before uploading any photo', async ({ page }) => {
  const mock = await mockWorkspace(page)
  await openCollection(page)
  await page.getByLabel('选择多页照片', { exact: true }).setInputFiles(['first.png', 'second.png'].map(name => ({ name, mimeType: 'image/png', buffer: png })))
  await page.locator('.ant-select').filter({ has: page.getByRole('combobox', { name: '照片 second.png 的答题页编号' }) }).click()
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').getByText('答题页 1', { exact: true }).click()
  await page.getByRole('checkbox', { name: '已逐张对照照片核对产物编号及答题页编号' }).check()
  await page.getByRole('button', { name: /上传所选照片/ }).click()
  await expect(page.getByText('同一批照片的答题页编号重复，请核对后上传', { exact: true })).toBeVisible()
  expect(mock.uploads()).toBe(0)
  expect(mock.unexpected).toEqual([])
})

test('page simulation stays inside photo collection and never enables submission', async ({ page }) => {
  const mock = await mockWorkspace(page, { vision: true })
  await openCollection(page)
  const start = page.getByRole('button', { name: '运行模拟提取', exact: true })
  await expect(start).toBeDisabled()
  await queue(page, ['page-1.png', 'page-2.png'])
  await expect(start).toBeEnabled()
  await start.click()
  await expect(page.getByText('模拟完成 · 待核对', { exact: true })).toHaveCount(2)
  await page.getByRole('button', { name: '查看本页记录', exact: true }).first().click()
  await expect(page.getByText('模拟记录不能作为真实识别或质量验收证据', { exact: true })).toBeVisible()
  await expect(page.getByText('第 3 题 · 第 1 小题 · 第 1 空：无法辨认', { exact: true })).toBeVisible()
  await page.getByRole('dialog', { name: '本页模拟提取记录' }).getByRole('button', { name: '关闭', exact: true }).click()
  await page.getByRole('button', { name: '重新模拟本页', exact: true }).first().click()
  await expect(page.getByText('答题页 1 · 第 2 次任务', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: /提交为待复核|确认并规则判分|正式采用/ })).toHaveCount(0)
  expect(mock.visionPosts()).toBe(1)
  expect(mock.unexpected).toEqual([])
})

test('retake preserves unchanged page task and moves old photo result to history', async ({ page }) => {
  const mock = await mockWorkspace(page, { vision: true })
  await openCollection(page)
  await queue(page, ['page-1.png', 'page-2.png'])
  await page.getByRole('button', { name: '运行模拟提取', exact: true }).click()
  await expect(page.getByText('模拟完成 · 待核对', { exact: true })).toHaveCount(2)
  await queue(page, ['retake.png'])
  await expect(page.getByText('模拟完成 · 待核对', { exact: true })).toHaveCount(1)
  await page.getByRole('button', { name: '运行模拟提取', exact: true }).click()
  await expect(page.getByText('模拟完成 · 待核对', { exact: true })).toHaveCount(2)
  await page.getByText('历史任务（1）', { exact: true }).click()
  await expect(page.getByText('照片已变化 · 结果失效', { exact: true })).toBeVisible()
  expect(mock.visionPosts()).toBe(2)
  expect(mock.unexpected).toEqual([])
})

test('real page extraction requires reviewed photos, shows costs and never enables grading', async ({ page }, testInfo) => {
  const mock = await mockWorkspace(page, { realVision: true })
  await openCollection(page)
  await queue(page, ['page-1.png', 'page-2.png'])
  const start = page.getByRole('button', { name: '运行真实提取', exact: true })
  await expect(start).toBeDisabled()
  const review = page.getByRole('checkbox', { name: '已核对当前照片中的姓名、学号等个人信息已遮挡，可发送至已配置的模型' })
  await review.check()
  await expect(start).toBeEnabled()
  await start.click()
  await expect(page.getByText('提取完成 · 待核对', { exact: true })).toHaveCount(2)
  await expect(page.getByText(/累计调用 2 \/ 30 次/)).toBeVisible()
  await page.getByRole('button', { name: '查看本页记录', exact: true }).first().click()
  await expect(page.getByText('逐页任务仅保存提取结果，请在下方整卷答案核对中确认录入；已录入的成绩请到批改页查看', { exact: true })).toBeVisible()
  await expect(page.getByText(/预估费用 0.0102 元/)).toBeVisible()
  await page.getByRole('dialog', { name: '本页提取记录' }).getByRole('button', { name: '关闭', exact: true }).click()
  await expect(page.getByRole('dialog', { name: '本页提取记录' })).not.toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('real-vision-page-tasks.png'), fullPage: true })
  await queue(page, ['retake.png'])
  await expect(review).not.toBeChecked()
  await expect(start).toBeDisabled()
  await expect(page.getByRole('button', { name: /提交为待复核|确认并规则判分|正式采用/ })).toHaveCount(0)
  expect(mock.visionPosts()).toBe(1)
  expect(mock.unexpected).toEqual([])
})

test('unknown usage blocks new paid extraction and missing config never falls back to fake', async ({ page }) => {
  let mock = await mockWorkspace(page, { realVision: true, usageUnknown: true })
  await openCollection(page)
  await queue(page, ['page-1.png', 'page-2.png'])
  await page.getByRole('checkbox', { name: '已核对当前照片中的姓名、学号等个人信息已遮挡，可发送至已配置的模型' }).check()
  await expect(page.getByText(/1 次用量待核对，已暂停后续真实调用/)).toBeVisible()
  await expect(page.getByRole('button', { name: '运行真实提取', exact: true })).toBeDisabled()
  expect(mock.visionPosts()).toBe(0)
  expect(mock.unexpected).toEqual([])
  await page.unroute('**/api/v1/**')
  mock = await mockWorkspace(page, { realVision: true, visionMisconfigured: true })
  await openCollection(page)
  await expect(page.getByText('多页视觉配置不可用', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '运行真实提取', exact: true })).toBeDisabled()
  await expect(page.getByRole('button', { name: '运行模拟提取', exact: true })).toHaveCount(0)
  expect(mock.unexpected).toEqual([])
})

test('merge, correct, save and explicitly submit whole-paper answers', async ({ page }, testInfo) => {
  const mock = await mockWorkspace(page, { realVision: true, transcription: true })
  await openCollection(page)
  await queue(page, ['page-1.png', 'page-2.png'])
  await page.getByRole('button', { name: '合并最新整卷提取结果', exact: true }).click()
  const editor = page.getByRole('textbox', { name: '第3题第1小题第1空答案', exact: true })
  await expect(editor).toHaveValue('-156')
  await editor.fill('-1/56')
  await expect(page.getByRole('button', { name: '确认录入并判分', exact: true })).toBeDisabled()
  for (const n of [1, 2]) await page.getByRole('checkbox', { name: `已对照照片核对答题页 ${n} 的编号和全部答案`, exact: true }).check()
  await page.getByRole('button', { name: '保存核对清单', exact: true }).click()
  await expect(page.getByRole('button', { name: /保存核对清单/ })).toBeDisabled()
  await page.getByRole('button', { name: '刷新核对清单（放弃未保存修改）', exact: true }).click()
  await expect(editor).toHaveValue('-1/56')
  await expect(page.getByText('模型原始抄读：已作答', { exact: true })).toHaveCount(2)
  expect(mock.submittedCount()).toBe(0)
  await page.getByRole('checkbox', { name: '确认已核对整卷，按保存的答案录入批改', exact: true }).check()
  await page.getByRole('button', { name: '确认录入并判分', exact: true }).click()
  await expect(page.getByRole('link', { name: '查看批改结果', exact: true })).toHaveAttribute('href', `/paper-instances/${paperId}/grading`)
  expect(mock.submittedCount()).toBe(1)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy()
  await page.screenshot({ path: testInfo.outputPath('whole-paper-transcription.png'), fullPage: true })
  expect(mock.unexpected).toEqual([])
})

test('retakes invalidate saved transcription and edits clear the page check', async ({ page }) => {
  const mock = await mockWorkspace(page, { realVision: true, transcription: true })
  await openCollection(page)
  await queue(page, ['page-1.png', 'page-2.png'])
  await page.getByRole('button', { name: '合并最新整卷提取结果', exact: true }).click()
  const check = page.getByRole('checkbox', { name: '已对照照片核对答题页 1 的编号和全部答案', exact: true })
  await check.check()
  await page.getByRole('textbox', { name: '第3题第1小题第1空答案', exact: true }).fill('1/56')
  await expect(check).not.toBeChecked()
  await page.getByRole('button', { name: '保存核对清单', exact: true }).click()
  await expect(page.getByRole('button', { name: '保存核对清单', exact: true })).toBeDisabled()
  await queue(page, ['retake.png'])
  await expect(page.getByText('核对清单已过期', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '确认录入并判分', exact: true })).toBeDisabled()
  await expect(page.getByRole('textbox', { name: '第3题第1小题第1空答案', exact: true })).toBeDisabled()
  expect(mock.submittedCount()).toBe(0)
  expect(mock.unexpected).toEqual([])
})

test('merge explains missing reviewer and audit fields can be completed beside the button', async ({ page }) => {
  const mock = await mockWorkspace(page, { realVision: true, transcription: true })
  await openCollection(page)
  await queue(page, ['page-1.png', 'page-2.png'])
  // Reload starts a new local review session; it must not silently reuse a name.
  await page.reload()
  await page.getByRole('combobox', { name: '纸面产物编号' }).click()
  await page.getByText(`${artifactCode} · r1 · 2 个答题页`, { exact: true }).click()
  const merge = page.getByRole('button', { name: '合并最新整卷提取结果', exact: true })
  await expect(merge).toBeDisabled()
  await expect(page.getByText('请填写本次整卷核对人。', { exact: true })).toBeVisible()
  await page.getByRole('textbox', { name: '整卷核对人', exact: true }).fill('lin')
  await expect(page.getByRole('textbox', { name: '核对人', exact: true })).toHaveValue('lin')
  await page.getByRole('textbox', { name: '整卷核对原因', exact: true }).fill(' ')
  await expect(merge).toBeDisabled()
  await expect(page.getByText('请填写至少2个字的整卷核对原因。', { exact: true })).toBeVisible()
  await page.getByRole('textbox', { name: '整卷核对原因', exact: true }).fill('合并核对')
  await expect(merge).toBeEnabled()
  await merge.click()
  await expect(page.getByRole('textbox', { name: '第3题第1小题第1空答案', exact: true })).toBeVisible()
  expect(mock.visionPosts()).toBe(0)
  expect(mock.submittedCount()).toBe(0)
  expect(mock.unexpected).toEqual([])
})

test('disabled stage or failed status never exposes the retired recognition flow', async ({ page }) => {
  let fail = true
  const requests: string[] = []
  await page.route('**/api/v1/**', route => {
    requests.push(new URL(route.request().url()).pathname)
    return route.fulfill({ status: fail ? 503 : 200, json: fail ? { message: '暂时不可用' } : { enabled: false } })
  })
  await page.goto(`/paper-instances/${paperId}/recognize`)
  await expect(page.getByText('答题页功能加载失败', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: /开始提取答案|上传图片|查看原单图识别记录/ })).toHaveCount(0)
  fail = false
  await page.getByRole('button', { name: /^重\s*试$/ }).click()
  await expect(page.getByText('答题页识别尚未启用', { exact: true })).toBeVisible()
  expect(requests.every(url => url.endsWith('/internal/stage6/status'))).toBe(true)
})
