import { expect, test } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/**', async (route) => {
    const response = await page.request.fetch(route.request())
    await route.fulfill({ response })
  })
})

test('question catalog supports finding and editing existing questions without single-question upload', async ({ page }) => {
  const qid = 'q.math.g6.expr.school.036'
  await page.goto('/questions')
  await expect(page.getByRole('heading', { name: '题库', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '上传新题', exact: true })).toHaveCount(0)
  await expect(page.getByRole('dialog')).toHaveCount(0)

  await page.getByLabel('关键词或 qid').fill(qid)
  await page.getByRole('button', { name: /^筛\s*选$/ }).click()
  const questionLink = page.getByRole('link', { name: qid, exact: true })
  await expect(questionLink).toHaveCount(1)
  await questionLink.click()
  await expect(page.getByRole('heading', { name: qid, exact: true })).toBeVisible()
  await page.getByRole('button', { name: '编辑题目内容', exact: true }).click()
  const editor = page.getByRole('dialog', { name: '编辑题目内容', exact: true })
  await expect(editor).toBeVisible()
  await expect(editor.getByRole('button', { name: '保存并重新审核', exact: true })).toBeVisible()
  await editor.getByRole('button', { name: /^取\s*消$/ }).click()
  await expect(editor).toHaveCount(0)
})
