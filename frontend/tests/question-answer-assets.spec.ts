import { expect, test } from '@playwright/test'

const api = process.env.M2_API_URL ?? 'http://localhost:8000/api/v1'

// Use Node's request client as a read-only bridge for the isolated test port;
// production CORS remains restricted to the normal frontend origin.
test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/**', async (route) => {
    const response = await page.request.fetch(route.request())
    await route.fulfill({ response })
  })
})

async function question080(page: import('@playwright/test').Page) {
  const list = await (await page.request.get(`${api}/questions?keyword=q.math.g6.rational.school.080`)).json()
  expect(list.total).toBe(1)
  const response = await page.request.get(`${api}/questions/${list.items[0].id}`)
  expect(response.ok()).toBeTruthy()
  return response.json()
}

test('080 displays three stored answers with final-result grading', async ({ page }) => {
  const question = await question080(page)
  await page.goto(`/questions/${question.id}`)
  const answers = page.locator('.ant-descriptions').first()
  await expect(answers.getByText('2023/2024', { exact: true })).toBeVisible()
  await expect(answers.getByText('4048/2025', { exact: true })).toBeVisible()
  await expect(answers.getByText('数值匹配', { exact: true })).toHaveCount(3)
  await expect(answers.getByText('—', { exact: true })).toHaveCount(0)
  await expect(answers).not.toContainText('[ASSET:')
  const after = await (await page.request.get(`${api}/questions/${question.id}`)).json()
  expect(after.review_revision).toBe(question.review_revision)
  expect(after.grading_readiness).toBe('raw_answer_only')
  await expect(page.getByText('审核答案并进入组卷池', { exact: true })).toHaveCount(0)
  await expect(page.getByText(/供后续视觉识别专项验证；本题不进入日常自动组卷池/)).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy()
})

test('legacy image references render a loadable image instead of an asset token', async ({ page }) => {
  const question = await question080(page)
  const path = 'assets/rational/q.math.g6.rational.school.080_rId687.png'
  const asset = question.assets.find((item: { path: string }) => item.path === path)
  expect(asset?.web_url).toBeTruthy()
  const shown = structuredClone(question)
  shown.correct_answer.items[0].answer = `[ASSET:${path}]`
  await page.route(`**/api/v1/questions/${question.id}`, (route) => route.fulfill({ json: shown }))
  await page.goto(`/questions/${question.id}`)
  const answers = page.locator('.ant-descriptions').first()
  const image = answers.locator('img.question-asset').first()
  await expect(image).toBeVisible()
  await expect(image).toHaveAttribute('src', asset.web_url)
  await expect.poll(() => image.evaluate((element) => (element as HTMLImageElement).naturalWidth)).toBeGreaterThan(0)
  await expect(answers).not.toContainText('[ASSET:')
})

test('expr036 displays the recovered original square diagram without broken resources', async ({ page }) => {
  const list = await (await page.request.get(`${api}/questions?keyword=q.math.g6.expr.school.036`)).json()
  expect(list.total).toBe(1)
  const url = `${api}/questions/${list.items[0].id}`
  const before = await (await page.request.get(url)).json()
  await page.goto(`/questions/${before.id}`)
  const diagram = page.locator('img.question-asset[src$="_canvas265.svg"]')
  await expect(diagram).toHaveCount(1)
  await expect(diagram).toBeVisible()
  await expect.poll(() => diagram.evaluate((element) => (element as HTMLImageElement).naturalWidth)).toBe(960)
  await expect(page.locator('.asset-error')).toHaveCount(0)
  expect(before.correct_answer.items.map((item: { item_id: string }) => item.item_id)).toEqual([
    'sub_1.blank_1', 'sub_2.blank_1', 'sub_3.blank_1', 'sub_4.blank_1',
  ])
  const answers = page.locator('.ant-descriptions').first()
  await expect(answers.getByText('第 4 小题 · 第 1 空', { exact: true })).toBeVisible()
  await expect(answers.getByText('第 2 小题 · 第 2 空', { exact: true })).toHaveCount(0)
  expect(before.question_text.match(/[（(]([1-4])[）)]/g)).toEqual(['（1）', '（2）', '（3）', '（4）'])
  const source = await (await page.request.get(await diagram.getAttribute('src') as string)).text()
  expect(source).toContain('第1个正方形')
  expect(source).toContain('>14</text>')
  expect(source).not.toContain('font-size="133350"')
  await diagram.screenshot({ path: `../.runtime/expr036-${test.info().project.name}.png` })
  const after = await (await page.request.get(url)).json()
  expect(after.review_revision).toBe(before.review_revision)
  expect(after.correct_answer).toEqual(before.correct_answer)
  expect(after.grading_readiness).toBe('machine_ready')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy()
})
