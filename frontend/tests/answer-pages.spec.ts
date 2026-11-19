import { expect, test, type Page } from '@playwright/test'

const paperId = '00000000-0000-4000-8000-000000000091'
const artifactId = '00000000-0000-4000-8000-000000000092'
const artifactCode = 'A123456789ABCDEF'
const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aHZkAAAAASUVORK5CYII=', 'base64')
const prefix = `/api/v1/internal/stage6/paper-instances/${paperId}/answer-card-artifacts`

async function mockWorkspace(page: Page, { failSecond = false, archived = false, noArtifacts = false } = {}) {
  const images: Array<{ id: string; available: boolean; image_url: string }> = []
  const history: Array<{ revision_no: number; page_no: number; action: string; before_image_id: string | null; after_image_id: string | null; reviewer: string; reason: string }> = []
  const selected: Record<number, string | null> = {}
  let exists = false, revision = 0, calls = 0
  let failure = failSecond
  const unexpected: string[] = []
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
  return { unexpected, uploads: () => calls }
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
