import { expect, test, type Page } from '@playwright/test'

const paperId = '00000000-0000-4000-8000-000000000301'
const itemId = '00000000-0000-4000-8000-000000000302'
const reference = { kind: 'group', items: [
  { item_id: 'sub_2.blank_2', subquestion_no: 2, blank_no: 2, answer: '相等', answer_match_type: 'exact' },
  { item_id: 'sub_5.blank_1', subquestion_no: 5, blank_no: 1, answer: '-1/56', answer_match_type: 'numeric' },
  { item_id: '9', answer: '负', answer_match_type: 'exact' },
] }

async function mockPaper(page: Page, submitted = false) {
  const writes: any[] = []
  const initialAnswer = { kind: 'group', items: reference.items.map(part => ({ item_id: part.item_id, answer: part.answer })) }
  const detail: Record<string, any> = {
    submission_id: submitted ? 'submitted-fixture' : null, paper_instance_id: paperId, assignment_id: 'fixture',
    assignment_title: '手动输入回归练习', instance_code: 'INPUT-FIXTURE', version_no: 1, paper_status: 'published',
    is_active: true, can_submit: !submitted, student_id: 'fixture', student_code: 'S001', student_name: '测试学生',
    source: submitted ? 'manual' : null, submitted_at: null, replayed: false, unchanged: false,
    answer_key_update_count: 0, regraded_count: 0,
    summary: { total_count: 1, correct_count: 0, incorrect_count: 0, needs_review_count: submitted ? 1 : 0 },
    items: [{ id: itemId, question_id: 'fixture', order_no: 3,
      question_snapshot: { question_text: '按对应小题和空位填写答案', question_type: 'fill_blank', original_qid: 'manual-fixture' },
      options_snapshot: null, answer_snapshot: reference, answer_rule_snapshot: { answer_match_type: 'group' },
      grading_answer_snapshot: reference, grading_answer_rule_snapshot: { answer_match_type: 'group' },
      standard_answer_source: 'published_snapshot', answer_key_update_available: false,
      knowledge_point_snapshot: { id: 'fixture', name: '有理数' }, assets: [],
      attempt: submitted ? attempt(initialAnswer) : null }], mastery_update_stats: null,
  }
  function attempt(answer: unknown) {
    return { id: 'attempt-fixture', paper_instance_item_id: itemId, order_no: 3, student_answer: answer,
      normalized_answer: null, grading_details: { reason_code: 'fixture', auto_status: 'needs_review' },
      grade_status: 'needs_review', is_correct: null, manual_override: false, revision_no: 1,
      answered_at: '2026-11-18T00:00:00Z', reviewed_at: null, used_in_profile: false,
      exclusion_reason: null, recognition_status: null, ocr_confidence: null }
  }
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname.replace('/api/v1', '')
    const method = route.request().method()
    const json = (body: unknown) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) })
    if (path === '/internal/stage6/status') return json({ enabled: false, provider: 'fake' })
    if (path === `/paper-instances/${paperId}/submission`) return json(detail)
    if (path === `/paper-instances/${paperId}/submissions/manual` && method === 'POST') {
      const body = route.request().postDataJSON(); writes.push(body)
      detail.can_submit = false; detail.submission_id = 'submitted-fixture'; detail.source = 'manual'
      detail.items[0].attempt = attempt(body.answers[0].answer)
      return json(detail)
    }
    if (method !== 'GET') writes.push({ unexpected: path })
    return route.fulfill({ status: 404, body: '{}' })
  })
  await page.goto(`/paper-instances/${paperId}/grading`)
  return writes
}

test('incomplete fractions retain placeholders and cannot be submitted as integers', async ({ page }) => {
  const writes = await mockPaper(page)
  const field = page.locator('.manual-answer-field-stack math-field')
  await expect(field).toBeVisible()
  await field.evaluate(element => {
    const mathfield = element as HTMLElement & { value: string }
    mathfield.value = '-\\frac{1}{\\placeholder{}}56'
    mathfield.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await expect(page.getByRole('alert').filter({ hasText: '公式尚未填写完整' })).toBeVisible()
  await expect(page.locator('.latex-source')).toContainText('\\placeholder')
  await page.getByRole('button', { name: '提交并自动判分', exact: true }).click()
  await expect(page.getByText('第 3 题公式尚未填写完整，请补充分子、分母或其他公式空位')).toBeVisible()
  expect(writes).toEqual([])
})

test('negative fractions survive numerator denominator typing, rerenders and input mode switches', async ({ page }) => {
  const writes = await mockPaper(page)
  const stack = page.locator('.group-answer-row[data-item-id="sub_5.blank_1"] .manual-answer-field-stack')
  const field = stack.locator('math-field')
  await expect(field).toBeVisible()
  await stack.getByRole('button', { name: '插入分数', exact: true }).click()
  await page.keyboard.type('-1')
  await expect.poll(() => field.evaluate(element => (element as HTMLElement & { getValue: (format: string) => string }).getValue('latex')))
    .toBe('\\frac{-1}{\\placeholder{}}')
  await page.keyboard.press('Tab')
  await page.keyboard.type('56')
  await expect(stack.locator('.latex-source')).toContainText('(-1)/(56)')
  await stack.getByText('普通输入', { exact: true }).click()
  await expect(stack.getByRole('textbox')).toHaveValue('(-1)/(56)')
  await stack.getByText('公式键盘', { exact: true }).click()
  await expect.poll(() => field.evaluate(element => (element as HTMLElement & { getValue: (format: string) => string }).getValue('latex')))
    .toContain('\\frac')
  await page.getByRole('button', { name: '提交并自动判分', exact: true }).click()
  await expect(page.getByText('学生原始答案', { exact: true })).toBeVisible()
  expect(writes).toHaveLength(1)
  expect(writes[0].answers[0].answer.items.find((part: { item_id: string }) => part.item_id === 'sub_5.blank_1').answer).toBe('(-1)/(56)')
})

test('historical student answers display canonical and legacy unit coordinates without metadata', async ({ page }) => {
  const writes = await mockPaper(page, true)
  const studentAnswers = page.getByRole('rowheader', { name: '学生原始答案', exact: true }).locator('..').locator(':scope > td').first()
  await expect(studentAnswers.getByRole('rowheader', { name: '第 2 小题 · 第 2 空', exact: true })).toBeVisible()
  await expect(studentAnswers.getByRole('rowheader', { name: '第 5 小题 · 第 1 空', exact: true })).toBeVisible()
  await expect(studentAnswers.getByRole('rowheader', { name: '第 9 小题 · 第 1 空', exact: true })).toBeVisible()
  await expect(studentAnswers.getByRole('rowheader', { name: '第 1 小题 · 第 1 空', exact: true })).toHaveCount(0)
  expect(writes).toEqual([])
})
