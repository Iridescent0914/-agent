import { expect, test, type Page } from '@playwright/test'

const api = process.env.M2_API_URL ?? 'http://localhost:8000/api/v1'
const dimensions = [
  '抽象能力', '运算能力', '几何直观', '空间观念', '推理能力',
  '数据观念', '模型观念', '应用意识', '创新意识',
]

test.beforeEach(async ({ page }) => {
  // The test server uses a separate port; relay local API requests as in the
  // existing read-only browser tests without changing deployment CORS rules.
  await page.route('**/api/v1/**', async (route) => {
    const response = await page.request.fetch(route.request())
    await route.fulfill({ response })
  })
})

async function watchAxisLabels(page: Page) {
  await page.addInitScript((names) => {
    const captured = new Set<string>()
    ;(window as unknown as { literacyAxisLabels: Set<string> }).literacyAxisLabels = captured
    const original = CanvasRenderingContext2D.prototype.fillText
    CanvasRenderingContext2D.prototype.fillText = function (text, x, y, maxWidth) {
      if (names.includes(text)) captured.add(text)
      if (maxWidth === undefined) original.call(this, text, x, y)
      else original.call(this, text, x, y, maxWidth)
    }
  }, dimensions)
}

async function studentId(page: Page) {
  const response = await page.request.get(`${api}/students?page_size=100`)
  expect(response.ok()).toBeTruthy()
  const body = await response.json()
  expect(body.items.length).toBeGreaterThan(0)
  return body.items[0].id as string
}

async function allAxesVisible(page: Page) {
  await expect.poll(() => page.evaluate(() => Array.from(
    (window as unknown as { literacyAxisLabels: Set<string> }).literacyAxisLabels,
  ).sort())).toEqual([...dimensions].sort())
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy()
}

test('required-question filter uses all formal dimensions including innovation', async ({ page }) => {
  const response = await page.request.get(`${api}/questions/literacy-dimensions`)
  expect(await response.json()).toEqual(dimensions)
  await page.goto('/assignments/new')
  const selector = page.getByRole('combobox', { name: '核心素养（可选，可多选）' })
  for (const name of ['创新意识', '空间观念', '数据观念']) {
    await selector.click()
    await page.locator('.ant-select-item-option-content').getByText(name, { exact: true }).click()
    await expect(page.locator('.ant-select-selection-item').filter({ hasText: name })).toBeVisible()
    await selector.press('Escape')
  }
  await selector.click()
  await expect(page.locator('.ant-select-item-option-content').getByText('数据意识', { exact: true })).toHaveCount(0)
})

test('student radar renders nine axes and initialized dimensions', async ({ page }, testInfo) => {
  const id = await studentId(page)
  const profile = await (await page.request.get(`${api}/students/${id}/profile`)).json()
  expect(profile.literacies.map((row: { name: string }) => row.name)).toEqual(dimensions)
  expect(profile.literacies.every((row: { rule_version: string }) => row.rule_version === 'literacy-dynamic-v2')).toBeTruthy()
  for (const name of ['空间观念', '数据观念']) {
    expect(profile.literacies.find((row: { name: string }) => row.name === name).score).toBe(60)
  }
  await watchAxisLabels(page)
  await page.goto(`/students/${id}`)
  await expect(page.getByRole('img', { name: '核心素养能力雷达图' })).toBeVisible()
  await allAxesVisible(page)
  await page.screenshot({ path: testInfo.outputPath('nine-dimensional-student.png'), fullPage: true })
})

test('class radar renders the same nine dimensions', async ({ page }) => {
  await watchAxisLabels(page)
  await page.goto('/analytics')
  await expect(page.getByRole('img', { name: '班级素养能力平均水平雷达图' })).toBeVisible()
  await allAxesVisible(page)
})

test('incomplete class averages are hidden instead of drawing missing values as zero', async ({ page }) => {
  const id = await studentId(page)
  await page.route('**/api/v1/analytics/class-overview**', async (route) => {
    const response = await route.fetch()
    const body = await response.json()
    body.literacies = body.literacies.filter((row: { name: string }) => row.name !== '数据观念')
    await route.fulfill({ response, json: body })
  })
  await page.goto(`/students/${id}`)
  const card = page.locator('.ant-card').filter({ has: page.getByText('素养能力水平', { exact: true }) })
  await expect(card.getByText('班级平均的素养维度或版本不一致，请重新加载班级数据。')).toBeVisible()
  await expect(card.getByRole('switch')).toHaveCount(0)
})
