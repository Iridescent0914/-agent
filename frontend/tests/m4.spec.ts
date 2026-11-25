import { expect, test, type Page } from '@playwright/test'
import type { SubmissionDetail } from '../src/types/submission'

const paperId = '00000000-0000-4000-8000-000000000041'
const submissionId = '00000000-0000-4000-8000-000000000042'
const image = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aHZkAAAAASUVORK5CYII=', 'base64')

function submission(): SubmissionDetail {
  return {
    paper_instance_id: paperId, assignment_id: 'assignment-fixture', assignment_title: '离线批改练习',
    student_id: 'student-fixture', student_code: 'S001', student_name: '测试学生',
    submission_id: submissionId, source: 'manual', can_submit: false, is_active: true,
    instance_code: 'M4-TEST', version_no: 1, paper_status: 'published',
    recognition_run_id: null, recognition_image_url: null, recognition_image_purged_at: null,
    recognition_provider: null, recognition_model: null, recognition_prompt_version: null,
    submitted_at: '2026-11-19T00:00:00Z', replayed: false, unchanged: false,
    answer_key_update_count: 0, regraded_count: 0, mastery_update_stats: null,
    summary: { total_count: 2, correct_count: 2, incorrect_count: 0, needs_review_count: 0 },
    items: [1, 2].map(n => ({
      id: `item-${n}`, question_id: `question-${n}`, order_no: n,
      question_snapshot: { original_qid: `fixture-${n}`, question_type: 'fill_blank', question_text: `离线题目${n}：____`, difficulty: 1 },
      options_snapshot: null, assets: [], knowledge_point_snapshot: { id: 'fixture-kp', name: '离线知识点' },
      answer_snapshot: n === 1 ? '30' : '相等', grading_answer_snapshot: n === 1 ? '30' : '相等',
      answer_rule_snapshot: { answer_match_type: n === 1 ? 'numeric' : 'exact' },
      grading_answer_rule_snapshot: { answer_match_type: n === 1 ? 'numeric' : 'exact' },
      standard_answer_source: 'published_snapshot', answer_key_update_available: false,
      attempt: { id: `attempt-${n}`, paper_instance_item_id: `item-${n}`, order_no: n,
        student_answer: n === 1 ? '+30' : '相等', normalized_answer: n === 1 ? 30 : '相等',
        grading_details: { auto_status: 'matched', reason_code: 'matched' }, grade_status: 'auto_confirmed', is_correct: true,
        manual_override: false, revision_no: 1, answered_at: '2026-11-19T00:00:00Z', reviewed_at: null,
        used_in_profile: true, exclusion_reason: null, recognition_status: null, ocr_confidence: null },
    })),
  }
}

async function workspace(page: Page, value = submission()) {
  const writes: string[] = [], unexpected: string[] = []
  await page.route('**/api/v1/**', async route => {
    const request = route.request(), url = new URL(request.url()).pathname
    if (request.method() !== 'GET') writes.push(`${request.method()} ${url}`)
    if (url.endsWith('/internal/stage6/status')) return route.fulfill({ json: { enabled: false } })
    if (url.endsWith('/classes')) return route.fulfill({ json: [] })
    if (url.endsWith('/grading/queue')) return route.fulfill({ json: { total: 1, items: [{
      paper_instance_id: paperId, assignment_id: value.assignment_id, assignment_title: value.assignment_title,
      class_id: null, class_name: null, student_id: value.student_id, student_code: value.student_code,
      student_name: value.student_name, version_no: 1, instance_code: value.instance_code, is_active: true,
      invalidation_type: null, grading_status: value.submission_id ? 'completed' : 'unsubmitted', item_count: 2,
      needs_review_count: value.summary.needs_review_count, submitted_at: value.submitted_at,
    }] } })
    if (url.endsWith(`/paper-instances/${paperId}/submission`)) return route.fulfill({ json: value })
    if (url.endsWith(`/paper-instances/${paperId}/accuracy-comparison`) && request.method() === 'GET') return route.fulfill({ status: 503, json: { message: 'comparison unavailable in grading fixture' } })
    if (url.endsWith('/regrade')) return route.fulfill({ json: { ...value, answer_key_update_count: 0, regraded_count: 1 } })
    if (url.endsWith('/fixture-image')) return route.fulfill({ contentType: 'image/png', body: image })
    unexpected.push(`${request.method()} ${url}`)
    return route.fulfill({ status: 500, json: { message: 'Unexpected offline API' } })
  })
  return { writes, unexpected }
}

async function noOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy()
}

test('grading queue opens an existing submission with traceable results', async ({ page }) => {
  const mock = await workspace(page)
  await page.goto('/grading')
  await expect(page.getByRole('heading', { name: '作业批改', exact: true })).toBeVisible()
  await page.goto(`/paper-instances/${paperId}/grading`)
  await expect(page.getByText('归一化结果', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('规则自动确认', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('画像证据', { exact: true }).first()).toBeVisible()
  await expect(page.getByRole('button', { name: /人工改判为错误/ }).first()).toBeVisible()
  await expect(page.getByRole('button', { name: /确认正确$/ })).toHaveCount(0)
  await page.getByRole('button', { name: '修正学生答案', exact: true }).first().click()
  await expect(page.getByRole('dialog')).toContainText('公式可直接使用公式键盘')
  await expect(page.getByRole('dialog').locator('math-field').first()).toBeVisible()
  await page.getByRole('dialog').getByRole('button', { name: /取\s*消/ }).click()
  await noOverflow(page)
  expect(mock.writes).toEqual([])
  expect(mock.unexpected).toEqual([])
})

test('grading workspace remains usable on a narrow viewport', async ({ page }) => {
  await workspace(page)
  await page.goto('/grading')
  await expect(page.getByRole('heading', { name: '作业批改', exact: true })).toBeVisible()
  await noOverflow(page)
})

test('republished answer key exposes an explicit regrade action', async ({ page }) => {
  const value = submission()
  value.answer_key_update_count = 1; value.items[0].answer_key_update_available = true
  const mock = await workspace(page, value)
  await page.goto(`/paper-instances/${paperId}/grading`)
  await expect(page.getByText('题库标准答案已重新发布', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '重新评分（1 题）', exact: true }).click()
  await expect(page.getByRole('dialog')).toContainText('追加修订记录')
  await page.getByRole('dialog').getByRole('button', { name: '重新评分', exact: true }).click()
  await expect(page.getByRole('button', { name: '重新评分（1 题）', exact: true })).toHaveCount(0)
  expect(mock.writes).toEqual([`POST /api/v1/submissions/${submissionId}/regrade`])
  expect(mock.unexpected).toEqual([])
})

test('an unsubmitted published paper exposes manual answer inputs without writing data', async ({ page }) => {
  const value = submission()
  value.submission_id = null; value.can_submit = true; value.source = null
  value.items.forEach(item => { item.attempt = null })
  const mock = await workspace(page, value)
  await page.goto(`/paper-instances/${paperId}/grading`)
  await expect(page.getByRole('button', { name: '提交并自动判分', exact: true })).toBeVisible()
  await expect(page.getByText('学生整题留空', { exact: true }).first()).toBeVisible()
  const textAnswer = page.locator('.manual-answer-field-stack').filter({ has: page.locator('input') }).first()
  await expect(textAnswer).toBeVisible()
  await textAnswer.getByText('公式键盘', { exact: true }).click()
  await expect(textAnswer.locator('math-field')).toBeVisible()
  const formulaAnswer = page.locator('.manual-answer-field-stack').filter({ has: page.locator('math-field') }).first()
  await expect(formulaAnswer.getByRole('button', { name: '打开完整公式键盘', exact: true })).toBeVisible()
  await formulaAnswer.getByRole('button', { name: '插入分数', exact: true }).click()
  const formula = formulaAnswer.locator('math-field')
  expect(await formula.evaluate(element => (element as HTMLElement & { getValue: (format: string) => string }).getValue('latex'))).toContain('\\frac')
  await formula.evaluate(element => {
    const field = element as HTMLElement & { value: string }
    field.value = '1/2'; field.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await expect(formulaAnswer.locator('.latex-source')).toContainText('1')
  await expect(formulaAnswer.locator('.latex-source')).toContainText('2')
  await noOverflow(page)
  expect(mock.writes).toEqual([])
  expect(mock.unexpected).toEqual([])
})

test('confirmed multipage submission shows submitted photos and normal grading review', async ({ page }) => {
  const value = submission()
  value.source = 'image'
  value.summary.needs_review_count = 1
  value.answer_transcription = { id: 'transcription-fixture', revision_no: 3, pages: [{
    page_no: 1, image_url: '/fixture-image', provider: 'bailian', model: 'offline-vision',
  }] }
  value.items[0].attempt!.grade_status = 'needs_review'
  value.items[0].attempt!.is_correct = null
  value.items[0].attempt!.grading_details.recognition_reviewed = true
  const mock = await workspace(page, value)
  await page.goto(`/paper-instances/${paperId}/grading`)
  await expect(page.getByText('答题页原图与核对来源', { exact: true })).toBeVisible()
  await expect(page.getByRole('img', { name: '答题页 1 提交原图', exact: true })).toBeVisible()
  await expect(page.getByText('答题原图已清理', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '批量确认清晰抄读项', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: /确认抄读并规则判分$/ })).toHaveCount(0)
  await expect(page.getByRole('button', { name: /确认正确$/ })).toBeVisible()
  await noOverflow(page)
  expect(mock.unexpected).toEqual([])
})

test('historical single-image submissions retain photo and transcription review access', async ({ page }) => {
  const value = submission()
  value.source = 'image'; value.recognition_run_id = 'old-run-fixture'; value.recognition_image_url = '/fixture-image'
  value.items[0].attempt!.grade_status = 'needs_review'; value.items[0].attempt!.is_correct = null
  value.items[0].attempt!.recognition_status = 'readable'; value.summary.needs_review_count = 1
  const mock = await workspace(page, value)
  await page.goto(`/paper-instances/${paperId}/grading`)
  await expect(page.getByText('答题区原图', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: /确认抄读并规则判分$/ })).toBeVisible()
  await expect(page.getByRole('button', { name: '批量确认清晰抄读项', exact: true })).toBeVisible()
  expect(mock.writes).toEqual([])
  expect(mock.unexpected).toEqual([])
})
