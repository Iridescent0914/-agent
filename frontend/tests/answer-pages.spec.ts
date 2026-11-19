import { expect, test, type Page } from '@playwright/test'
import type { PageVisionTask } from '../src/types/visionPages'

const paperId = '00000000-0000-4000-8000-000000000091'
const artifactId = '00000000-0000-4000-8000-000000000092'
const artifactCode = 'A123456789ABCDEF'
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aHZkAAAAASUVORK5CYII=', 'base64')
const prefix = `/api/v1/internal/stage6/paper-instances/${paperId}/answer-card-artifacts`

async function mockWorkspace(page: Page, { failSecond = false, archived = false, noArtifacts = false, vision = false } = {}) {
  const images: Array<{ id: string; available: boolean; image_url: string }> = []
  const history: Array<{ revision_no: number; page_no: number; action: string; before_image_id: string | null; after_image_id: string | null; reviewer: string; reason: string }> = []
  const selected: Record<number, string | null> = {}
  let exists = false, revision = 0, calls = 0
  let failure = failSecond
  const unexpected: string[] = []
  const visionTasks: PageVisionTask[] = []
  let visionPosts = 0
  const visionView = () => {
    const rows = visionTasks.map(t => ({ ...t,
      is_current: selected[t.page_no] === t.image_id && !visionTasks.some(newer => newer.page_no === t.page_no && newer.generation > t.generation && newer.image_id === t.image_id),
      effective_status: selected[t.page_no] === t.image_id ? t.status : 'stale',
    }))
    return { configuration: { provider: 'fake', synthetic: true, error_code: null, real_provider_available: false },
      collection_id: 'collection-test', collection_revision: revision,
      current_tasks: rows.filter(t => t.is_current).sort((a, b) => a.page_no - b.page_no), history: rows, can_submit: false }
  }
  const newVisionTask = (number: number, generation = 1) => {
    const task: PageVisionTask = { id: `vision-${visionTasks.length + 1}`, page_no: number,
      image_id: selected[number]!, generation, revision_no: 3, status: 'needs_review', effective_status: 'needs_review',
      is_current: true, synthetic: true, provider: 'fake', model: 'fake-stage6-vision-page-v2',
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
    if (url.endsWith('/page-collection/vision-tasks')) {
      if (request.method() === 'POST') {
        visionPosts++
        if (!vision) return route.fulfill({ status: 500, json: { message: 'unexpected vision POST' } })
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
        calls: [{ id: 'call-test', call_no: 1, status: 'succeeded', raw_response: '{"synthetic":true}', usage: { cost_cny: 0 }, late_response: false }],
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
  return { unexpected, uploads: () => calls, visionPosts: () => visionPosts }
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
  await expect(page.getByText('答题页照片已收齐，尚未识别', { exact: true })).toBeVisible()
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
  await expect(page.getByText('答题页照片已收齐，尚未识别', { exact: true })).toBeVisible()
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
