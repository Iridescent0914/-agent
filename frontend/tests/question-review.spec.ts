import { expect, test } from '@playwright/test'

const api = process.env.M2_API_URL ?? 'http://localhost:8000/api/v1'

test('pending fill question opens formula-aware review editor without writing data', async ({ page }) => {
  const response = await page.request.get(
    `${api}/questions?question_type=fill_blank&grading_readiness=raw_answer_only&page_size=1`,
  )
  const catalog = await response.json()
  const question = catalog.items[0]
  expect(question).toBeDefined()
  const before = await (await page.request.get(`${api}/questions/${question.id}`)).json()

  await page.goto('/questions?question_type=fill_blank&grading_readiness=raw_answer_only&page_size=1')
  await expect(page.getByRole('button', { name: '去审核', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '去审核', exact: true }).click()
  await expect(page).toHaveURL(new RegExp(`/questions/${question.id}$`))
  await expect(page.getByRole('button', { name: '审核答案并进入组卷池' })).toBeVisible()
  await page.getByRole('button', { name: '审核答案并进入组卷池' }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.getByText('保存审核草稿', { exact: true })).toBeVisible()

  const firstItem = page.locator('.answer-item-editor').first()
  await firstItem.locator('.ant-select').first().click()
  await page.locator('.ant-select-dropdown:visible').getByText('公式（LaTeX 精确匹配）', { exact: true }).click()
  await expect(firstItem.locator('math-field')).toBeVisible()
  await expect(page.getByText(/保存的 LaTeX/).first()).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy()

  await page.locator('.ant-drawer-close').click()
  await expect(page.getByRole('dialog')).toBeHidden()
  const after = await (await page.request.get(`${api}/questions/${question.id}`)).json()
  expect(after.review_revision).toBe(before.review_revision)
  expect(after.grading_readiness).toBe('raw_answer_only')
})

test('ready question exposes withdraw and edit entry without changing it on cancel', async ({ page }) => {
  const response = await page.request.get(
    `${api}/questions?question_type=fill_blank&grading_readiness=machine_ready&page_size=1`,
  )
  const catalog = await response.json()
  const question = catalog.items[0]
  const before = await (await page.request.get(`${api}/questions/${question.id}`)).json()

  await page.goto('/questions?question_type=fill_blank&grading_readiness=machine_ready&page_size=1')
  await expect(page.getByRole('button', { name: '撤回/编辑', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '撤回/编辑', exact: true }).click()
  await expect(page.getByRole('button', { name: '撤回并重新编辑' })).toBeVisible()
  await page.getByRole('button', { name: '撤回并重新编辑' }).click()
  await expect(page.getByText('这道题当前在自动组卷池中', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '确认撤回到待审核' })).toBeVisible()
  await page.locator('.ant-drawer-close').click()

  const after = await (await page.request.get(`${api}/questions/${question.id}`)).json()
  expect(after.review_revision).toBe(before.review_revision)
  expect(after.grading_readiness).toBe('machine_ready')
})
