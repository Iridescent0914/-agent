import { expect, test } from '@playwright/test'

const api = process.env.M2_API_URL ?? 'http://localhost:8000/api/v1'


async function noOverflow(page: import('@playwright/test').Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy()
}


async function ensureUnsubmittedPaper(page: import('@playwright/test').Page) {
  const current = await (await page.request.get(`${api}/grading/queue?status=unsubmitted`)).json()
  if (current.items.length) return current.items[0]
  const students = await (await page.request.get(`${api}/students?page_size=100`)).json()
  const created = await page.request.post(`${api}/assignments`, { data: {
    idempotency_key: `m4-e2e-unsubmitted-${Date.now()}`,
    title: 'M4 E2E 未提交试卷',
    student_ids: [students.items[0].id],
    recommendation_parameters: {
      review_exam_point_codes: [
        'math.g6.rational.exam.local.a61b42d3fd',
        'math.g6.rational.exam.local.dae9bb3283',
      ],
      review_count: 2,
      avoid_recent_days: 0,
      max_exam_point_ratio: 1,
    },
  } })
  expect(created.status()).toBe(201)
  const paper = (await created.json()).papers[0]
  const published = await page.request.post(`${api}/paper-instances/${paper.id}/publish`, {
    data: { expected_edit_revision: paper.edit_revision },
  })
  expect(published.ok()).toBeTruthy()
  return { paper_instance_id: paper.id }
}


test('grading queue opens an existing submission with traceable results', async ({ page }) => {
  await page.goto('/grading')
  await expect(page.getByRole('heading', { name: '作业批改', exact: true })).toBeVisible()
  const link = page.getByRole('link', { name: '查看批改', exact: true }).first()
  await expect(link).toBeVisible()
  await link.click()
  await expect(page.getByText('归一化结果', { exact: true }).first()).toBeVisible()
  await expect(page.getByText(/^(规则自动确认|教师已确认)$/).first()).toBeVisible()
  await expect(page.getByText('画像证据', { exact: true }).first()).toBeVisible()
  await expect(page.getByRole('button', { name: /(人工)?改判为(正确|错误)/ }).first()).toBeVisible()
  await expect(page.getByRole('button', { name: '确认正确', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '确认错误', exact: true })).toHaveCount(0)
  await expect(page.getByText('修订历史', { exact: true }).first()).toBeVisible()
  const paperId = page.url().match(/paper-instances\/([^/]+)\/grading/)?.[1]
  expect(paperId).toBeTruthy()
  const detail = await (await page.request.get(`${api}/paper-instances/${paperId}/submission`)).json()
  const formulaItemIndex = detail.items.findIndex((item: {
    answer_snapshot: unknown
    answer_rule_snapshot: { answer_match_type?: string }
  }) => {
    const snapshot = item.answer_snapshot as {
      kind?: string
      items?: Array<{ answer_match_type?: string }>
    }
    return item.answer_rule_snapshot.answer_match_type === 'numeric'
      || item.answer_rule_snapshot.answer_match_type === 'latex_exact'
      || snapshot.items?.some((part) => part.answer_match_type === 'numeric' || part.answer_match_type === 'latex_exact')
  })
  expect(formulaItemIndex).toBeGreaterThanOrEqual(0)
  await page.getByRole('button', { name: '修正学生答案', exact: true }).nth(formulaItemIndex).click()
  await expect(page.getByRole('dialog')).toContainText('公式可直接使用公式键盘')
  await expect(page.getByRole('dialog').locator('math-field').first()).toBeVisible()
  await page.getByRole('dialog').getByRole('button', { name: /取\s*消/ }).click()
  await noOverflow(page)
})


test('grading workspace remains usable on a narrow viewport', async ({ page }) => {
  await page.goto('/grading')
  await expect(page.getByRole('heading', { name: '作业批改', exact: true })).toBeVisible()
  await noOverflow(page)
})


test('republished answer key exposes an explicit regrade action', async ({ page }) => {
  const queue = await (await page.request.get(`${api}/grading/queue`)).json()
  const submitted = queue.items.find((item: { grading_status: string }) => item.grading_status !== 'unsubmitted')
  expect(submitted).toBeDefined()
  const detail = await (await page.request.get(
    `${api}/paper-instances/${submitted.paper_instance_id}/submission`,
  )).json()
  expect(detail.submission_id).toBeTruthy()
  const pending = {
    ...detail,
    answer_key_update_count: 1,
    items: detail.items.map((item: Record<string, unknown>, index: number) => ({
      ...item,
      answer_key_update_available: index === 0,
    })),
  }
  await page.route(
    `**/api/v1/paper-instances/${submitted.paper_instance_id}/submission`,
    (route) => route.fulfill({ json: pending }),
  )
  await page.route(
    `**/api/v1/submissions/${detail.submission_id}/regrade`,
    (route) => route.fulfill({
      json: { ...pending, answer_key_update_count: 0, regraded_count: 1 },
    }),
  )

  await page.goto(`/paper-instances/${submitted.paper_instance_id}/grading`)
  await expect(page.getByText('题库标准答案已重新发布', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '重新评分（1 题）', exact: true }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toContainText('追加修订记录')
  await dialog.getByRole('button', { name: '重新评分', exact: true }).click()
  await expect(page.getByRole('button', { name: '重新评分（1 题）', exact: true })).toHaveCount(0)
})


test('an unsubmitted published paper exposes manual answer inputs without writing data', async ({ page }) => {
  const target = await ensureUnsubmittedPaper(page)
  await page.goto(`/paper-instances/${target.paper_instance_id}/grading`)
  await expect(page.getByRole('button', { name: '提交并自动判分', exact: true })).toBeVisible()
  await expect(page.getByText('学生整题留空', { exact: true }).first()).toBeVisible()
  const textAnswer = page.locator('.manual-answer-field-stack').filter({ has: page.locator('input') }).first()
  await expect(textAnswer).toBeVisible()
  await textAnswer.getByText('公式键盘', { exact: true }).click()
  await expect(textAnswer.locator('math-field')).toBeVisible()
  const formulaAnswer = page.locator('.manual-answer-field-stack').filter({ has: page.locator('math-field') }).first()
  await expect(formulaAnswer.getByRole('button', { name: '打开完整公式键盘', exact: true })).toBeVisible()
  const formula = formulaAnswer.locator('math-field')
  await expect(formula).toBeVisible()
  await formulaAnswer.getByRole('button', { name: '插入分数', exact: true }).click()
  expect(await formula.evaluate((element) => (
    element as HTMLElement & { getValue: (format: string) => string }
  ).getValue('latex'))).toContain('\\frac')
  await formula.evaluate((element) => {
    const field = element as HTMLElement & { value: string }
    field.value = '1/2'
    field.dispatchEvent(new Event('input', { bubbles: true }))
  })
  const expression = formulaAnswer.locator('.latex-source')
  await expect(expression).toContainText('1')
  await expect(expression).toContainText('2')
  await noOverflow(page)
  const after = await (await page.request.get(`${api}/paper-instances/${target.paper_instance_id}/submission`)).json()
  expect(after.submission_id).toBeNull()
})
