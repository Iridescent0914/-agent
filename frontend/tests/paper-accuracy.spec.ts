import { expect, test, type Page } from '@playwright/test'

const paper = '00000000-0000-4000-8000-000000000801'

async function workspace(page: Page, mode: 'normal' | 'empty' | 'wrong' | 'failed' | 'partial' | 'inactive' = 'normal') {
  let correct = true
  let reads = 0
  const writes: string[] = [], unexpected: string[] = []
  const submission = () => ({
    paper_instance_id: paper, submission_id: 'sub', assignment_id: 'assignment', assignment_title: '比较示例',
    instance_code: 'ACCURACY', version_no: 1, paper_status: 'published', is_active: mode !== 'inactive',
    can_submit: false, student_id: 'student', student_code: 'C001', student_name: '比较样例', source: 'manual',
    answer_key_update_count: 0, summary: { total_count: 1, correct_count: correct ? 1 : 0, incorrect_count: correct ? 0 : 1, needs_review_count: 0 },
    items: [{ id: 'item', question_id: 'question', order_no: 1,
      question_snapshot: { question_type: 'fill_blank', original_qid: 'comparison-test', question_text: '填写答案' },
      answer_snapshot: '1', grading_answer_snapshot: '1', options_snapshot: null,
      answer_rule_snapshot: { answer_match_type: 'exact' }, grading_answer_rule_snapshot: { answer_match_type: 'exact' },
      standard_answer_source: 'published_snapshot', answer_key_update_available: false,
      knowledge_point_snapshot: { id: 'kp1', name: '有理数' }, assets: [],
      attempt: { id: 'attempt', paper_instance_item_id: 'item', order_no: 1, student_answer: '1', normalized_answer: '1',
        grading_details: {}, grade_status: correct ? 'auto_confirmed' : 'teacher_confirmed', is_correct: correct,
        revision_no: correct ? 1 : 2, manual_override: !correct, used_in_profile: true, answered_at: '2026-11-25T00:00:00Z' },
    }],
  })
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname.replace('/api/v1', '')
    const method = route.request().method()
    if (method === 'PATCH' && path === '/attempts/attempt/review') {
      writes.push(path); correct = false
      return route.fulfill({ json: submission() })
    }
    if (method !== 'GET') {
      unexpected.push(`${method} ${path}`)
      return route.fulfill({ status: 500, json: { message: 'unexpected mutation' } })
    }
    if (path === '/internal/stage6/status') return route.fulfill({ json: { enabled: false } })
    if (path === `/paper-instances/${paper}/submission`) return route.fulfill({ json: submission() })
    if (path === `/paper-instances/${paper}/accuracy-comparison`) {
      reads++
      if (mode === 'failed') return route.fulfill({ status: 503, json: { message: 'unavailable' } })
      const empty = mode === 'empty', partial = mode === 'partial', inactive = mode === 'inactive'
      return route.fulfill({ json: {
        paper_instance_id: paper, computed_at: '2026-11-25T00:00:00Z', counting_unit: 'attempt_question_group',
        history_scope: 'all_history_excluding_current_paper', read_only: true,
        data_status: inactive ? 'inactive_paper' : empty ? 'no_history' : partial ? 'incomplete_paper' : 'available',
        paper: { total_question_count: partial ? 3 : 1, unsubmitted_question_count: partial ? 1 : 0,
          confirmed_attempt_count: inactive ? 0 : 1, correct_count: inactive || !correct ? 0 : 1, incorrect_count: inactive || correct ? 0 : 1,
          distinct_question_count: 1, pending_review_count: partial ? 1 : 0, excluded_attempt_count: inactive ? 1 : 0,
          accuracy: inactive ? null : correct ? 1 : 0, judged_coverage: inactive ? 0 : partial ? 1 / 3 : 1 },
        history: { confirmed_attempt_count: empty ? 0 : 10, correct_count: empty || mode === 'wrong' ? 0 : 8,
          incorrect_count: empty ? 0 : mode === 'wrong' ? 10 : 2, distinct_question_count: empty ? 0 : 7,
          pending_review_count: 2, excluded_attempt_count: 1, accuracy: empty ? null : mode === 'wrong' ? 0 : .8 },
        knowledge_coverage: { seen_question_count: empty ? 0 : 1, unseen_question_count: partial || empty ? 1 : 0, unmapped_question_count: partial ? 1 : 0,
          points: [
            { knowledge_point_code: 'kp1', knowledge_point_name: '有理数', paper_question_count: 1, historical_confirmed_attempt_count: empty ? 0 : 8, history_status: empty ? 'unseen' : 'seen' },
            ...(partial ? [
              { knowledge_point_code: 'kp2', knowledge_point_name: '新知识点', paper_question_count: 1, historical_confirmed_attempt_count: 0, history_status: 'unseen' },
              { knowledge_point_code: null, knowledge_point_name: '知识点未映射', paper_question_count: 1, historical_confirmed_attempt_count: 0, history_status: 'unmapped' },
            ] : []),
          ] },
      } })
    }
    unexpected.push(`${method} ${path}`)
    return route.fulfill({ status: 404, json: { message: 'unexpected request' } })
  })
  await page.goto(`/paper-instances/${paper}/grading`)
  const card = page.getByLabel('本卷与历史表现参考', { exact: true })
  await expect(card).toBeVisible()
  return { card, writes, unexpected, reads: () => reads }
}

test('read-only comparison refreshes after a grade revision and fits mobile', async ({ page }, testInfo) => {
  const mock = await workspace(page)
  const stats = mock.card.locator('.ant-statistic')
  await expect(stats.nth(0)).toContainText('100.0')
  await expect(stats.nth(1)).toContainText('80.0')
  await expect(mock.card).toContainText('排除本卷的历史：已确认 10 次')
  await expect(mock.card).toContainText('尚未设定最小样本量')
  await mock.card.getByText('查看知识点历史覆盖', { exact: true }).click()
  await expect(mock.card).toContainText('有理数：本卷 1 题组，其他试卷已确认作答 8 次')
  expect(mock.writes).toEqual([])
  const before = mock.reads()
  await page.getByPlaceholder('复核或改判原因（至少 2 个字）').fill('核对后改判')
  await page.getByRole('button', { name: /人工改判为错误/ }).click()
  await expect(stats.nth(0)).toContainText('0.0')
  await expect(stats.nth(1)).toContainText('80.0')
  expect(mock.reads()).toBe(before + 1)
  expect(mock.writes).toEqual(['/attempts/attempt/review'])
  expect(mock.unexpected).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy()
  await mock.card.screenshot({ path: testInfo.outputPath('paper-accuracy.png') })
})

test('unknown baseline is a dash; confirmed wrong baseline is zero percent', async ({ page }) => {
  let mock = await workspace(page, 'empty')
  await expect(mock.card).toContainText('排除本卷后暂无有效已确认历史作答')
  await expect(mock.card.locator('.ant-statistic').nth(1)).toContainText('—')
  await expect(mock.card.locator('.ant-statistic').nth(1).locator('.ant-statistic-content-suffix')).toHaveCount(0)
  expect(mock.writes).toEqual([]); expect(mock.unexpected).toEqual([])
  await page.unroute('**/api/v1/**')
  mock = await workspace(page, 'wrong')
  await expect(mock.card.locator('.ant-statistic').nth(1)).toContainText('0.0')
  await expect(mock.card.locator('.ant-statistic').nth(1).locator('.ant-statistic-content-suffix')).toHaveText('%')
  expect(mock.writes).toEqual([]); expect(mock.unexpected).toEqual([])
})

test('partial and inactive papers cannot look like a complete valid comparison', async ({ page }) => {
  let mock = await workspace(page, 'partial')
  await expect(mock.card).toContainText('本卷尚未全部有效判定')
  await expect(mock.card).toContainText('待复核 1、未录入 1')
  await expect(mock.card.locator('.ant-statistic').nth(2)).toContainText('33.3')
  expect(mock.writes).toEqual([]); expect(mock.unexpected).toEqual([])
  await page.unroute('**/api/v1/**')
  mock = await workspace(page, 'inactive')
  await expect(mock.card).toContainText('该试卷未发布或已失效')
  await expect(mock.card.locator('.ant-statistic').first()).toContainText('—')
  expect(mock.writes).toEqual([]); expect(mock.unexpected).toEqual([])
})

test('comparison failure does not block grading, and refresh remains a GET', async ({ page }) => {
  const mock = await workspace(page, 'failed')
  await expect(mock.card).toContainText('比较统计暂不可用')
  await expect(page.getByRole('button', { name: /人工改判为错误/ })).toBeEnabled()
  const before = mock.reads()
  await mock.card.getByRole('button', { name: '刷新统计' }).click()
  await expect.poll(mock.reads).toBe(before + 1)
  expect(mock.writes).toEqual([]); expect(mock.unexpected).toEqual([])
})
