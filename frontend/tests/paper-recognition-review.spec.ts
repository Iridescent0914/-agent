import { expect, test, type Page } from '@playwright/test'

const paper = '00000000-0000-4000-8000-000000000901'
const artifact = '00000000-0000-4000-8000-000000000902'
const base = `/internal/stage6/paper-instances/${paper}/answer-card-artifacts`
const scope = `${base}/${artifact}/page-collection`
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aHZkAAAAASUVORK5CYII=', 'base64')

async function workspace(page: Page, mode: 'normal' | 'missing' | 'conflict' | 'fake' = 'normal') {
  let generation = 1, revision = 0
  let current: any = { review_status: 'pending', is_error: null }
  const history: any[] = [], writes: Array<{ path: string; body: any }> = [], unexpected: string[] = []
  const digest = () => String(generation).repeat(64)
  const review = () => ({ recognition_sha256: digest(), revision_no: revision, transcription_revision_no: 3,
    target_scope: 'submitted', can_record: true, stale: false, message: null,
    original_images_available: mode !== 'missing', synthetic: mode === 'fake', page_count: 1,
    pages: [{ page_no: 1, generation, provider: mode === 'fake' ? 'fake' : 'bailian', model: 'offline-fixture' }],
    current, history: history.map(h => ({ ...h, is_current_target: h.recognition_sha256 === digest() })),
  })
  await page.route('**/api/v1/**', async route => {
    const req = route.request(), path = new URL(req.url()).pathname.replace('/api/v1', '')
    const json = (body: unknown, status = 200) => route.fulfill({ status, json: body })
    if (path === `${scope}/recognition-review`) {
      if (req.method() === 'GET') return json(review())
      if (req.method() === 'PUT') {
        const body = req.postDataJSON(); writes.push({ path, body })
        if (mode === 'conflict') return json({ message: '整卷识别核对记录已被修改，请刷新后再保存' }, 409)
        expect(body.expected_recognition_sha256).toBe(digest())
        expect(body.expected_revision_no).toBe(revision)
        revision++
        current = { review_status: body.review_status,
          is_error: body.review_status === 'correct' ? false : body.review_status === 'incorrect' ? true : null,
          all_pages_reviewed: body.all_pages_reviewed, reviewer: body.reviewer, reason: body.reason }
        history.unshift({ ...current, recognition_sha256: digest(), revision_no: revision,
          synthetic: mode === 'fake', created_at: '2026-11-25T00:00:00Z' })
        return json(review())
      }
    }
    if (req.method() !== 'GET') { unexpected.push(`${req.method()} ${path}`); return json({}, 500) }
    if (path === '/internal/stage6/status') return json({ enabled: true })
    if (path.endsWith('/submission')) return json({ student_code: 'Q001', instance_code: 'QUALITY', version_no: 1, is_active: true })
    if (path === base) return json([{ id: artifact, artifact_code: 'A901', revision_no: 1, total_pages: 1 }])
    if (path === scope) return json({ id: 'collection', artifact_id: artifact, artifact_code: 'A901', revision_no: 1,
      total_pages: 1, status: 'collected', missing_page_nos: [], collection_ready: true, is_active: true,
      effective_input_sha256: 'a'.repeat(64), recognition_status: 'not_started', can_submit: false,
      pages: [{ page_no: 1, effective_image_id: 'image', status: 'uploaded' }],
      images: [{ id: 'image', available: true, image_url: `/api/v1${scope}/images/image` }], history: [] })
    if (path === `${scope}/images/image`) return route.fulfill({ contentType: 'image/png', body: png })
    if (path === `${scope}/vision-tasks`) return json({ configuration: { provider: 'fake', synthetic: true },
      current_tasks: [], history: [], collection_revision: 1, can_submit: false })
    if (path === `${scope}/transcription`) return json({ id: 'transcription', revision_no: 3, submission_id: 'submission',
      input_sha256: 'a'.repeat(64), can_submit: false, confirmed_page_nos: [1], history: [],
      pages: [{ page_no: 1, image_url: `/api/v1${scope}/images/image`, synthetic: false, identity_check: { status: 'match' } }],
      units: [{ slot_key: 'slot', order_no: 3, subquestion_no: 1, blank_no: 1, page_no: 1,
        source_module: 'required', recognition_status: 'readable', answer: '2x', original_status: 'readable', original_answer: 'x+x', confidence: .9 }] })
    unexpected.push(`${req.method()} ${path}`); return json({}, 404)
  })
  await page.goto(`/paper-instances/${paper}/recognize`)
  await page.getByRole('combobox', { name: '纸面产物编号' }).click()
  await page.getByText('A901 · r1 · 1 个答题页', { exact: true }).click()
  const panel = page.getByLabel('整卷识别质量核对', { exact: true })
  await expect(panel.getByText('已保存结论：', { exact: true })).toBeVisible()
  return { panel, writes, unexpected, nextVersion: () => {
    generation++; revision = 0; current = { review_status: 'pending', is_error: null }
  } }
}

async function choose(page: Page, value: string) {
  await page.locator('.ant-select').filter({ has: page.getByRole('combobox', { name: '整卷识别核对结论' }) }).click()
  await page.locator('.ant-select-dropdown:visible').getByText(value, { exact: true }).click()
}

async function audit(page: Page) {
  await page.getByRole('textbox', { name: '识别质量核对人', exact: true }).fill('测试教师')
  await page.getByRole('textbox', { name: '识别质量核对说明', exact: true }).fill('核对原图发现分数误抄')
}

test('quality review is explicit, independent from saved corrections, and persists after refresh', async ({ page }, testInfo) => {
  const mock = await workspace(page)
  await expect(mock.panel.getByText('未核对', { exact: true }).first()).toBeVisible()
  expect(mock.writes).toHaveLength(0)
  await expect(page.getByRole('textbox', { name: '整卷核对人', exact: true })).toBeDisabled()
  await choose(page, '发现错误'); await audit(page)
  const save = mock.panel.getByRole('button', { name: '保存识别核对记录', exact: true })
  await expect(save).toBeDisabled()
  await mock.panel.getByRole('checkbox', { name: '已对照全部答题页照片，核对模型原始抄录及页码归属' }).check()
  await save.click()
  await expect(mock.panel).toContainText('整卷识别核对记录已保存，答案和评分未改变')
  expect(mock.writes).toHaveLength(1)
  expect(mock.writes[0].body.review_status).toBe('incorrect')
  expect(mock.writes[0].body.all_pages_reviewed).toBe(true)
  expect(Object.keys(mock.writes[0].body).sort()).toEqual([
    'all_pages_reviewed', 'expected_recognition_sha256', 'expected_revision_no', 'reason', 'review_status', 'reviewer',
  ])
  await mock.panel.getByRole('button', { name: '刷新识别核对记录' }).click()
  await expect(mock.panel).toContainText('上次记录：测试教师')
  await expect(page.getByRole('textbox', { name: '识别质量核对人' })).toHaveValue('')
  await mock.panel.getByText('查看整卷识别核对历史（最近1条）', { exact: true }).click()
  await expect(mock.panel).toContainText('当前识别版本 · 核对 v1 · 发现错误')
  expect(mock.unexpected).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy()
  await mock.panel.screenshot({ path: testInfo.outputPath('paper-recognition-review.png') })
})

test('a new recognition version starts pending and preserves the prior quality history', async ({ page }) => {
  const mock = await workspace(page)
  await choose(page, '确认无误'); await audit(page)
  await mock.panel.getByRole('checkbox').check()
  await mock.panel.getByRole('button', { name: '保存识别核对记录' }).click()
  await expect(mock.panel).toContainText('核对记录 v1')
  mock.nextVersion()
  await mock.panel.getByRole('button', { name: '刷新识别核对记录' }).click()
  await expect(mock.panel).toContainText('核对记录 v0')
  await mock.panel.getByText('查看整卷识别核对历史（最近1条）', { exact: true }).click()
  await expect(mock.panel).toContainText('历史识别版本 · 核对 v1 · 确认无误')
  expect(mock.writes).toHaveLength(1); expect(mock.unexpected).toEqual([])
})

test('unavailable originals permit only unknown quality and cannot silently mark no error', async ({ page }) => {
  const mock = await workspace(page, 'missing')
  await choose(page, '确认无误'); await audit(page)
  await expect(mock.panel.getByRole('checkbox')).toBeDisabled()
  await expect(mock.panel.getByRole('button', { name: '保存识别核对记录' })).toBeDisabled()
  await choose(page, '无法判断')
  await mock.panel.getByRole('button', { name: '保存识别核对记录' }).click()
  await expect(mock.panel).toContainText('核对记录 v1')
  expect(mock.writes[0].body.review_status).toBe('uncertain')
  expect(mock.writes[0].body.all_pages_reviewed).toBe(false)
  expect(mock.unexpected).toEqual([])
})

test('conflicting saves keep the form for review and never write grades', async ({ page }) => {
  const mock = await workspace(page, 'conflict')
  await choose(page, '无法判断'); await audit(page)
  await mock.panel.getByRole('button', { name: '保存识别核对记录' }).click()
  await expect(mock.panel).toContainText('整卷识别核对记录已被修改')
  await expect(mock.panel).toContainText('核对记录 v0')
  expect(mock.writes).toHaveLength(1); expect(mock.unexpected).toEqual([])
})

test('synthetic quality records retain their explicit development label', async ({ page }) => {
  const mock = await workspace(page, 'fake')
  await expect(mock.panel).toContainText('模拟结果，仅供开发核对')
  await choose(page, '未核对'); await audit(page)
  await mock.panel.getByRole('button', { name: '保存识别核对记录' }).click()
  await expect(mock.panel).toContainText('核对记录 v1')
  expect(mock.unexpected).toEqual([])
})
