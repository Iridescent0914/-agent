import { expect, test, type Page } from '@playwright/test'

const api = process.env.M2_API_URL ?? 'http://localhost:8000/api/v1'

async function openStudent(page: Page) {
  const response = await page.request.get(`${api}/students`)
  const { items } = await response.json()
  await page.goto(`/students/${items[0].id}`)
  await expect(page.getByRole('heading', { name: items[0].display_name, exact: true })).toBeVisible()
  return items
}

async function assertNoPageOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy()
}

test('profile charts, snapshot evidence, history pagination and student switching', async ({ page }, testInfo) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  const students = await openStudent(page)
  await expect(page.getByRole('heading', { name: '重点考点诊断', exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: '全部考点', exact: true })).toBeHidden()
  await expect(page.locator('.profile-chart canvas')).toHaveCount(2)
  await expect.poll(async () => page.locator('.profile-chart canvas').evaluateAll((elements) => elements.map((element) => {
    const canvas = element as HTMLCanvasElement
    const data = canvas.getContext('2d')!.getImageData(0, 0, canvas.width, canvas.height).data
    let colored = 0
    for (let index = 3; index < data.length; index += 4) if (data[index] > 0) colored++
    return colored > 500
  }))).toEqual([true, true])
  await assertNoPageOverflow(page)
  await page.screenshot({ path: testInfo.outputPath('profile.png'), fullPage: true })
  await page.getByText(/查看该考点的逐次作答/).click()
  await page.getByRole('button', { name: '查看作答', exact: true }).first().click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.getByText('原题快照', { exact: true })).toBeVisible()
  await expect(page.getByText('当时标准答案', { exact: true })).toBeVisible()
  await page.locator('.ant-drawer-close').click()
  await expect(page.getByRole('dialog')).toBeHidden()
  await page.getByText('完整画像与作答证据', { exact: true }).click()
  await expect(page.getByRole('heading', { name: '全部考点', exact: true })).toBeVisible()
  await assertNoPageOverflow(page)

  await page.getByRole('tab', { name: '历史作答', exact: true }).click()
  await expect(page.getByText('共 30 条', { exact: true })).toBeVisible()
  await page.locator('.ant-pagination-next button').click()
  await expect(page.locator('.ant-pagination-item-active')).toHaveText('2')
  const attempts = (await (await page.request.get(`${api}/students/${students[0].id}/attempts?page_size=100`)).json()).items
  let withAsset: { original_qid: string; main_exam_point_name: string } | undefined
  for (const attempt of attempts) {
    const detail = await (await page.request.get(`${api}/students/${students[0].id}/attempts/${attempt.id}`)).json()
    if (detail.assets.length) { withAsset = attempt; break }
  }
  expect(withAsset).toBeDefined()
  const examCatalog = await (await page.request.get(`${api}/exam-points?chapter_code=math.g6.rational`)).json()
  const currentExam = examCatalog.find((item: { code: string }) => item.code === (withAsset as { main_exam_point_code: string }).main_exam_point_code)
  await page.getByRole('combobox', { name: '历史考点筛选' }).fill(currentExam.name)
  await page.locator('.ant-select-dropdown:visible').getByText(currentExam.name, { exact: true }).click()
  await page.getByRole('button', { name: withAsset!.original_qid, exact: true }).click()
  await expect(page.getByRole('dialog').locator('img').first()).toBeVisible()
  await expect.poll(() => page.getByRole('dialog').locator('img').evaluateAll((images) => images.every((image) => (image as HTMLImageElement).complete && (image as HTMLImageElement).naturalWidth > 0))).toBeTruthy()
  await page.screenshot({ path: testInfo.outputPath('evidence.png'), fullPage: true })
  await page.locator('.ant-drawer-close').click()
  await expect(page.getByRole('dialog')).toBeHidden()
  await page.locator('.student-heading .ant-select-selector').click()
  await page.locator('.ant-select-dropdown:visible').getByText(`${students[1].student_code} ${students[1].display_name}`, { exact: true }).click()
  await expect(page.getByRole('heading', { name: students[1].display_name, exact: true })).toBeVisible()
  await expect(page.getByRole('tab', { name: '画像', exact: true })).toHaveAttribute('aria-selected', 'true')
  await assertNoPageOverflow(page)
  expect(errors).toEqual([])
})

test('recommendation validation, real preview, reasons and shortages', async ({ page }, testInfo) => {
  await openStudent(page)
  await page.getByRole('tab', { name: '推荐练习', exact: true }).click()
  await page.getByRole('button', { name: /预览推荐/ }).click()
  await expect(page.getByText('推荐预览失败', { exact: true })).toBeVisible()
  const catalog = await (await page.request.get(`${api}/exam-points?chapter_code=math.g6.rational`)).json()
  const sufficient = catalog.find((item: { available_question_count: number }) => item.available_question_count >= 6)
  await page.locator('.recommendation-scope-grid .ant-select-selector').nth(1).click()
  await page.getByRole('combobox', { name: '回顾范围', exact: true }).fill(sufficient.name)
  await page.locator('.ant-select-dropdown:visible').getByText(`${sufficient.name}（${sufficient.available_question_count} 题）`, { exact: true }).click()
  await page.getByRole('spinbutton', { name: /单考点最大占比/ }).fill('1')
  await page.getByRole('heading', { level: 3 }).click()
  await page.getByRole('button', { name: /预览推荐/ }).click()
  await expect(page.getByText('请求 6 题，推荐 6 题，缺口 0 题', { exact: true })).toBeVisible()
  await page.locator('.ant-table-row-expand-icon').first().click()
  await expect(page.getByText('加权贡献', { exact: true })).toBeVisible()
  await assertNoPageOverflow(page)
  await page.screenshot({ path: testInfo.outputPath('recommendations.png'), fullPage: true })
  await page.getByRole('spinbutton', { name: /避免重复天数/ }).fill('3650')
  await page.getByRole('button', { name: /预览推荐/ }).click()
  await expect(page.getByText('请求 6 题，推荐 0 题，缺口 6 题', { exact: true })).toBeVisible()
  await page.getByText('回顾候选筛选：0 / 6 题', { exact: true }).click()
  await expect(page.getByText('近期已练', { exact: true }).first()).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('shortage.png'), fullPage: true })
})

test('profile request error can be retried', async ({ page }) => {
  await page.route('**/students/*/profile', (route) => route.fulfill({ status: 503, contentType: 'application/json', body: '{"message":"验收模拟请求失败"}' }))
  const students = (await (await page.request.get(`${api}/students`)).json()).items
  await page.goto(`/students/${students[0].id}`)
  await expect(page.getByText('学生详情加载失败', { exact: true })).toBeVisible()
  await page.unroute('**/students/*/profile')
  await page.getByRole('button', { name: '刷新画像', exact: true }).click()
  await expect(page.getByRole('heading', { name: students[0].display_name, exact: true })).toBeVisible()
})
