import { expect, test, type Page } from '@playwright/test'

const paperId = '00000000-0000-4000-8000-000000000201'
const itemId = '00000000-0000-4000-8000-000000000202'
const taskId = '00000000-0000-4000-8000-000000000203'
const reference = { kind: 'group', items: [
  { item_id: 'sub_1.blank_1', subquestion_no: 1, blank_no: 1, answer: 'x+x', answer_match_type: 'latex_exact' },
  { item_id: 'sub_2.blank_1', subquestion_no: 2, blank_no: 1, answer: '1', answer_match_type: 'numeric' },
] }

async function installMock(page: Page, enabled = true, real = false) {
  const officialWrites: string[] = []
  const feedbackWrites: Record<string, unknown>[] = []
  let task: Record<string, any> | null = null
  let revision = 0
  const history: Record<string, unknown>[] = []
  let current: Record<string, unknown> = { review_status: 'unreviewed', is_error: null }
  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url())
    const method = route.request().method()
    const path = url.pathname.replace('/api/v1', '')
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) })
    if (path === `/paper-instances/${paperId}/submission`) return json({
      submission_id: 'submitted-fixture', paper_instance_id: paperId, assignment_id: 'fixture', assignment_title: 'S2 逐空等价建议练习',
      instance_code: 'S2-FIXTURE-01', version_no: 1, paper_status: 'published', is_active: true, can_submit: false,
      student_id: 'fixture', student_code: 'S001', student_name: '样例学生', source: 'manual',
      submitted_at: '2026-11-18T00:00:00Z', replayed: false, unchanged: false,
      answer_key_update_count: 0, regraded_count: 0,
      summary: { total_count: 1, correct_count: 0, incorrect_count: 1, needs_review_count: 0 },
      items: [{ id: itemId, question_id: 'fixture', order_no: 3,
        question_snapshot: { question_text: '按空位填写答案', question_type: 'fill_blank', original_qid: 'S2-fixture' },
        options_snapshot: null, answer_snapshot: reference, answer_rule_snapshot: { answer_match_type: 'group' },
        grading_answer_snapshot: reference, grading_answer_rule_snapshot: { answer_match_type: 'group' },
        standard_answer_source: 'published_snapshot', answer_key_update_available: false,
        knowledge_point_snapshot: { id: 'fixture', name: '代数式' }, assets: [],
        attempt: { id: 'attempt-fixture', paper_instance_item_id: itemId, order_no: 3,
          student_answer: { kind: 'group', items: [{ item_id: 'sub_1.blank_1', answer: '2x' }, { item_id: 'sub_2.blank_1', answer: '2' }] },
          normalized_answer: null, grading_details: { reason_code: 'mismatched', auto_status: 'auto_confirmed' },
          grade_status: 'auto_confirmed', is_correct: false, manual_override: false, revision_no: 1,
          answered_at: '2026-11-18T00:00:00Z', reviewed_at: null, used_in_profile: true,
          exclusion_reason: null, recognition_status: null, ocr_confidence: null } }], mastery_update_stats: null,
    })
    if (path === '/internal/stage6/status') return json({ enabled, provider: real ? 'bailian' : 'fake', model: real ? 'qwen3.7-flash' : null,
      real_provider_available: real, budget: real ? { calls: task ? 1 : 0, max_calls: 20, estimated_cost_cny: task ? 0.00018 : 0,
        reserved_cost_cny: 0, max_budget_cny: 1, unknown_calls: 0 } : null })
    if (path === '/internal/stage6/equivalence-preview') {
      const body = route.request().postDataJSON()
      expect(body.student_answer).toBeNull()
      expect(body.expected_attempt_revision).toBe(1)
      return json({ eligible: body.item_id === 'sub_1.blank_1', item_id: body.item_id, input_revision: task ? 1 : 0,
        reason_code: body.item_id === 'sub_1.blank_1' ? 'formula_candidate' : 'rule_incorrect',
        message: body.item_id === 'sub_1.blank_1' ? '本空规则尚未确认，可请求等价建议' : '规则已有明确结果，请按现有判分或人工复核处理',
        reference_answer: body.item_id === 'sub_1.blank_1' ? 'x+x' : '1', student_answer: body.item_id === 'sub_1.blank_1' ? '2x' : '2' })
    }
    if (path === '/internal/stage6/equivalence-tasks' && method === 'GET') return json(task ? [task] : [])
    if (path === '/internal/stage6/equivalence-tasks' && method === 'POST') {
      const body = route.request().postDataJSON()
      expect(body.item_id).toBe('sub_1.blank_1')
      expect(body.expected_input_revision).toBe(0)
      task = { id: taskId, status: 'queued', input_revision: 1, attempt_count: 0, max_attempts: 3,
        retryable: false, synthetic: !real, provider: real ? 'bailian' : 'fake', model: real ? 'qwen3.7-flash' : 'fake-stage6-v1', error_code: null,
        created_at: '2026-11-18T00:00:00Z', snapshot: { item_id: 'sub_1.blank_1', subquestion_no: 1, blank_no: 1 },
        request_payload: { reference_answer: 'x+x', student_answer: '2x' }, result: null }
      return json(task)
    }
    if (path === `/internal/stage6/equivalence-tasks/${taskId}`) {
      task = { ...task, status: 'succeeded', attempt_count: 1,
        result: { verdict: real ? 'equivalent' : 'uncertain', explanation: real ? '两段表达式恒等。' : 'S1 fake仅验证调用链路，未执行数学等价判断。' } }
      return json(task)
    }
    if (path.includes('/quality-feedback/equivalence/')) {
      if (method === 'PUT') {
        const body = route.request().postDataJSON()
        expect(body.expected_revision).toBe(revision)
        feedbackWrites.push(body)
        revision += 1
        current = { ...body, is_error: body.review_status === 'incorrect' ? true : body.review_status === 'correct' ? false : null }
        history.push({ ...current, revision_no: revision })
      }
      return json({ revision_no: revision, input_stale: false, current, history })
    }
    if (method !== 'GET') officialWrites.push(path)
    return json({ message: 'unexpected request' }, 404)
  })
  return { officialWrites, feedbackWrites }
}

test('candidate preview, fake suggestion and per-unit quality feedback preserve final grading', async ({ page }, testInfo) => {
  const observed = await installMock(page)
  await page.goto(`/paper-instances/${paperId}/grading`)
  await page.getByRole('button', { name: '答案等价建议', exact: true }).click()
  const drawer = page.getByRole('dialog', { name: '第 3 题 · 答案等价建议' })
  await expect(drawer.getByText('模拟模式：验证逐空建议和质量核对流程')).toBeVisible()
  await expect(drawer.getByRole('button', { name: '生成模拟建议' })).toBeDisabled()
  await drawer.getByRole('button', { name: '检查本空是否需要建议' }).click()
  await expect(drawer.getByText('本空规则尚未确认，可请求等价建议')).toBeVisible()
  await drawer.getByRole('button', { name: '生成模拟建议' }).click()
  await expect(drawer.getByText('建议：无法确定', { exact: true })).toBeVisible()
  await drawer.locator('.ant-select[aria-label="模型质量核对状态"]').click()
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').filter({ hasText: /^确认错误$/ }).click()
  await drawer.getByLabel('质量核对人').fill('开发核对员')
  await drawer.getByLabel('质量核对原因').fill('合成样例验证人工正确结果保存')
  await drawer.getByRole('button', { name: '保存质量反馈' }).click()
  await expect(drawer.getByText('当前：确认错误 · 反馈修订 1')).toBeVisible()
  expect(observed.feedbackWrites[0].gold).toEqual({ verdict: 'equivalent' })
  expect(observed.feedbackWrites[0]).not.toHaveProperty('is_error')
  await drawer.locator('.ant-select[aria-label="选择作答空位"]').click()
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').filter({ hasText: /^小题 2 · 第 1 空$/ }).click()
  await expect(drawer.getByText('本空尚无建议记录')).toBeVisible()
  await drawer.getByRole('button', { name: '检查本空是否需要建议' }).click()
  await expect(drawer.getByText('规则已有明确结果，请按现有判分或人工复核处理')).toBeVisible()
  await expect(drawer.getByRole('button', { name: '生成模拟建议' })).toBeDisabled()
  expect(observed.officialWrites).toEqual([])
  expect(await drawer.evaluate((element) => element.scrollWidth <= element.clientWidth + 1)).toBeTruthy()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy()
  await page.screenshot({ path: testInfo.outputPath('equivalence-review.png') })
  await page.reload()
  await page.getByRole('button', { name: '答案等价建议', exact: true }).click()
  await expect(drawer.getByText('当前：确认错误 · 反馈修订 1')).toBeVisible()
})

test('feature off leaves the grading page without the internal suggestion entry', async ({ page }) => {
  await installMock(page, false)
  await page.goto(`/paper-instances/${paperId}/grading`)
  await expect(page.getByRole('heading', { name: 'S001 · 样例学生', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '答案等价建议', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '整卷等价判断', exact: true })).toHaveCount(0)
})

test('configured text model shows real mode and returns an unadopted suggestion', async ({ page }) => {
  const observed = await installMock(page, true, true)
  await page.goto(`/paper-instances/${paperId}/grading`)
  await page.getByRole('button', { name: '答案等价建议', exact: true }).click()
  const drawer = page.getByRole('dialog', { name: '第 3 题 · 答案等价建议' })
  await expect(drawer.getByText('文本模型：qwen3.7-flash')).toBeVisible()
  await drawer.getByRole('button', { name: '检查本空是否需要建议' }).click()
  await expect(drawer.getByText('本空规则尚未确认，可请求等价建议')).toBeVisible()
  await drawer.getByRole('button', { name: '生成等价建议（调用模型）' }).click()
  await expect(drawer.getByText('建议：等价', { exact: true })).toBeVisible()
  await expect(drawer.getByText('旧版记录：仅比较答案', { exact: true })).toBeVisible()
  await expect(drawer.getByText('模拟结果', { exact: true })).toHaveCount(0)
  expect(observed.officialWrites).toEqual([])
})
