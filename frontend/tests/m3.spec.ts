import { expect, test } from '@playwright/test'

const api = process.env.M2_API_URL ?? 'http://localhost:8000/api/v1'

async function noOverflow(page: import('@playwright/test').Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy()
}

test('assignment navigation and multi-student preview stay read-only', async ({ page }, testInfo) => {
  const before = await (await page.request.get(`${api}/assignments?page_size=100`)).json()
  await page.goto('/assignments')
  await expect(page.getByRole('heading', { name: '个性化作业', exact: true })).toBeVisible()
  await page.getByRole('link', { name: '创建作业', exact: true }).click()
  await expect(page.getByRole('heading', { name: '创建个性化作业', exact: true })).toBeVisible()
  await expect(page.locator('.assignment-form-grid .ant-select').first().locator('.ant-select-selection-item')).toHaveCount(5)

  const catalog = await (await page.request.get(`${api}/exam-points?chapter_code=math.g6.rational`)).json()
  const sufficient = catalog.find((item: { available_question_count: number }) => item.available_question_count >= 6)
  await page.getByLabel('回顾范围').click()
  await page.getByRole('combobox', { name: '回顾范围' }).fill(sufficient.name)
  await page.locator('.ant-select-dropdown:visible').getByText(`${sufficient.name}（${sufficient.available_question_count}题）`, { exact: true }).click()
  await page.getByRole('spinbutton', { name: '单考点最大占比' }).fill('1')
  await page.getByRole('spinbutton', { name: '避免近期重复天数' }).fill('0')
  await page.getByRole('button', { name: '预检全部学生' }).click()
  await expect(page.getByText(/名学生可完整生成/)).toBeVisible()
  await expect(page.getByText('预检不会写入作业', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '生成草稿' })).toBeVisible()
  await noOverflow(page)
  await page.screenshot({ path: testInfo.outputPath('assignment-preview.png'), fullPage: true })

  const after = await (await page.request.get(`${api}/assignments?page_size=100`)).json()
  expect(after.total).toBe(before.total)
})

test('assignment creation page has no global overflow on narrow viewport', async ({ page }) => {
  await page.goto('/assignments/new')
  await expect(page.getByRole('heading', { name: '创建个性化作业', exact: true })).toBeVisible()
  await noOverflow(page)
})
