import { expect, test, type Page } from '@playwright/test'

const paper = '00000000-0000-4000-8000-000000000501'
const item = '00000000-0000-4000-8000-000000000502'
const prefix = `/internal/stage6/paper-instances/${paper}`
const hash = 'a'.repeat(64)
const taskIds = ['00000000-0000-4000-8000-000000000503', '00000000-0000-4000-8000-000000000504']
const answers = ['一样', '负数', '+30']
const reference = { kind: 'group', items: ['相等', '负', '30'].map((answer, i) => ({
  item_id: `sub_${i + 1}.blank_1`, subquestion_no: i + 1, blank_no: 1, answer, answer_match_type: i === 2 ? 'numeric' : 'exact',
})) }

async function mock(page: Page, mode: 'real' | 'fake' | 'uncertain' | 'stale' | 'mixed' | 'context_missing' | 'adopted_mixed' | 'historical' = 'real') {
  let started = mode === 'fake' || mode === 'uncertain' || mode === 'mixed'
  let running = false
  const seededAdoption = mode === 'adopted_mixed' || mode === 'historical'
  let adopted = seededAdoption
  const fixtureReference = seededAdoption ? { ...reference, items: reference.items.map((unit, i) => ({
    ...unit, item_id: ['sub_1.blank_2', 'sub_2.blank_4', 'sub_2.blank_5'][i],
    subquestion_no: i === 0 ? 1 : 2, blank_no: i === 0 ? 2 : i + 3,
  })) } : reference
  const schedules: Record<string, unknown>[] = []
  const adoptions: Record<string, unknown>[] = []
  const unexpected: string[] = []
  const submission = () => ({
    submission_id: '00000000-0000-4000-8000-000000000505', paper_instance_id: paper,
    assignment_id: 'fixture', assignment_title: 'S5整卷判断练习', instance_code: 'S5-FIXTURE', version_no: 1,
    paper_status: 'published', is_active: true, can_submit: false, student_id: 'fixture', student_code: 'S001',
    student_name: '测试学生', source: 'image', submitted_at: '2026-11-20T00:00:00Z',
    summary: { total_count: 1, correct_count: adopted && !seededAdoption ? 1 : 0,
      incorrect_count: mode === 'historical' || adopted && !seededAdoption ? 0 : 1, needs_review_count: mode === 'historical' ? 1 : 0 },
    answer_key_update_count: 0, items: [{ id: item, question_id: 'fixture', order_no: 3,
      question_snapshot: { question_type: 'fill_blank', question_text: '填写短答案', original_qid: 'S5-fixture' },
      answer_snapshot: fixtureReference, grading_answer_snapshot: fixtureReference, options_snapshot: null,
      answer_rule_snapshot: { answer_match_type: 'group' }, grading_answer_rule_snapshot: { answer_match_type: 'group' },
      standard_answer_source: 'published_snapshot', answer_key_update_available: false,
      knowledge_point_snapshot: { name: '有理数' }, assets: [], attempt: {
        id: 'attempt-fixture', paper_instance_item_id: item, order_no: 3,
        student_answer: { kind: 'group', items: answers.map((answer, i) => ({ item_id: fixtureReference.items[i].item_id, answer })) },
        normalized_answer: null, grading_details: { reason_code: 'mismatched', auto_status: 'auto_confirmed', recognition_reviewed: true,
          ...(adopted ? { equivalence_review: {
            version: 'stage6-contextual-teacher-adoption-v2', judgment_mode: 'contextual', applied_revision_no: 2,
            item_ids: fixtureReference.items.slice(0, 2).map(unit => unit.item_id),
            decisions: Object.fromEntries([1, 0].map(i => [fixtureReference.items[i].item_id, {
              task_id: taskIds[i], verdict: seededAdoption && i === 1 ? 'not_equivalent' : 'equivalent',
            }])), reviewer: '测试教师', reason: '对照原图及标答核对',
          } } : {}),
        },
        grade_status: mode === 'historical' ? 'needs_review' : adopted ? 'teacher_confirmed' : 'auto_confirmed',
        is_correct: mode === 'historical' ? null : adopted && !seededAdoption, manual_override: adopted,
        revision_no: mode === 'historical' ? 3 : adopted ? 2 : 1, used_in_profile: mode !== 'historical',
        answered_at: '2026-11-20T00:00:00Z', reviewed_at: null,
      } }],
  })
  const report = () => ({ paper_instance_id: paper, input_sha256: hash,
    candidate_count: adopted || mode === 'context_missing' ? 0 : 2, new_task_count: started || mode === 'context_missing' ? 0 : 2,
    units: fixtureReference.items.map((p, i) => ({ paper_instance_item_id: item, item_id: p.item_id,
      order_no: 3, subquestion_no: i + 1, blank_no: 1, eligible: !adopted && i < 2 && mode !== 'context_missing',
      reason_code: i === 2 ? 'rule_correct' : 'short_text_candidate',
      message: i === 2 ? '数值规则已确认本空正确' : mode === 'context_missing' ? '本空上下文包含未文本化的题图或公式，请人工复核本题' : '精确文本未匹配，可请求短文本等价建议',
      reference_answer: p.answer, student_answer: answers[i], rule_result: { is_correct: i === 2 },
      answer_context: i < 2 && mode !== 'context_missing' ? { scope: 'subquestion',
        question_text: `有理数法则：\n（${i + 1}）本题填写【本空】。`, target_subquestion_no: i + 1, target_blank_no: 1 } : null,
      task: i < 2 && started ? { id: taskIds[i], status: running ? 'queued' : 'succeeded',
        synthetic: mode === 'fake', error_code: null, adoptable: mode !== 'fake' && mode !== 'uncertain' && !(mode === 'mixed' && i === 1) && !running,
        adoption_blocker: mode === 'mixed' && i === 1 ? { code: 'model_uncertain', message: '模型返回“无法确定”，请使用本题原有人工复核' } : null,
        result: running ? null : { schema_version: 'stage6-equivalence-response-v2', verdict: mode === 'uncertain' || mode === 'fake' || (mode === 'mixed' && i === 1) ? 'uncertain' : 'equivalent',
          explanation: '离线模型结果夹具' } } : null,
    })),
  })
  await page.route('**/api/v1/**', async route => {
    const path = new URL(route.request().url()).pathname.replace('/api/v1', '')
    const method = route.request().method()
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) })
    if (path === `/paper-instances/${paper}/submission`) return json(submission())
    if (path === `/paper-instances/${paper}/accuracy-comparison` && method === 'GET') return json({ message: 'comparison unavailable in grading fixture' }, 503)
    if (path === '/internal/stage6/status') return json({ enabled: true, provider: mode === 'fake' ? 'fake' : 'bailian',
      model: 'offline-text-model', real_provider_available: mode !== 'fake',
      budget: { calls: started ? 2 : 0, max_calls: 20, estimated_cost_cny: 0, max_budget_cny: 1, unknown_calls: 0 } })
    if (path === `${prefix}/equivalence`) { running = false; return json(report()) }
    if (path === `${prefix}/equivalence-tasks`) {
      schedules.push(route.request().postDataJSON())
      if (mode === 'stale') return json({ code: 'INPUT_STALE', message: '作答已变化，请重新检查整卷' }, 409)
      started = true; running = true
      return json({ ...report(), created_count: 2, reused_count: 0 })
    }
    if (path === `${prefix}/equivalence-adoptions`) {
      adoptions.push(route.request().postDataJSON()); adopted = true
      return json(submission())
    }
    unexpected.push(`${method} ${path}`)
    return json({ message: 'unexpected offline fixture request' }, 404)
  })
  return { schedules, adoptions, unexpected }
}

async function open(page: Page) {
  await page.goto(`/paper-instances/${paper}/grading`)
  await page.getByRole('button', { name: '整卷等价判断', exact: true }).click()
  const drawer = page.getByRole('dialog', { name: '整卷等价判断', exact: true })
  await drawer.getByRole('button', { name: '检查或刷新整卷' }).click()
  await expect(drawer.getByText('共 3 空，需等价判断 2 空，需新建任务', { exact: false })).toBeVisible()
  await drawer.getByLabel('整卷判断核对人').fill('测试教师')
  await drawer.getByLabel('整卷判断原因').fill('对照原图及标答核对')
  return drawer
}

test('whole-paper preview, explicit paid dispatch, polling and teacher adoption preserve original answers', async ({ page }) => {
  const observed = await mock(page)
  const drawer = await open(page)
  await expect(page.getByText('已采用建议', { exact: true })).toHaveCount(0)
  expect(observed.schedules).toHaveLength(0)
  await drawer.getByText('查看本空题干与位置', { exact: true }).first().click()
  await expect(drawer.getByText('使用试卷发布时的题干', { exact: false }).first()).toBeVisible()
  await expect(drawer.getByText('本题填写【本空】', { exact: false }).first()).toBeVisible()
  await drawer.getByRole('button', { name: '统一发起等价判断（调用模型）' }).click()
  const start = page.getByRole('dialog', { name: '确认统一发起等价判断' })
  await expect(start.getByText('后台会调用已配置', { exact: false })).toBeVisible()
  await start.getByRole('button', { name: '返回核对' }).click()
  expect(observed.schedules).toHaveLength(0)
  await drawer.getByRole('button', { name: '统一发起等价判断（调用模型）' }).click()
  await start.getByRole('button', { name: '确认发起', exact: true }).click()
  await expect(drawer.getByRole('button', { name: '采用第 3 题建议' })).toBeEnabled()
  await expect(drawer.getByText('建议：本空可接受', { exact: false })).toHaveCount(2)
  expect(observed.schedules).toHaveLength(1)
  expect(observed.schedules[0]).toEqual({ expected_input_sha256: hash, reviewer: '测试教师', reason: '对照原图及标答核对' })
  await expect(drawer.getByRole('button', { name: '统一发起等价判断（调用模型）' })).toBeDisabled()
  await drawer.getByText('查看跳过的空位与原因').click()
  await expect(drawer.getByText('数值规则已确认本空正确', { exact: false })).toBeVisible()
  await drawer.getByRole('button', { name: '采用第 3 题建议' }).click()
  const confirm = page.getByRole('dialog', { name: '确认采用第 3 题建议' })
  await confirm.getByRole('button', { name: '确认采用并更新评分' }).click()
  await expect(page.getByText('已采用本题建议，评分与画像已更新', { exact: true })).toBeVisible()
  expect(observed.adoptions).toHaveLength(1)
  expect(observed.adoptions[0].task_ids).toEqual(taskIds)
  await drawer.getByRole('button', { name: '检查或刷新整卷' }).click()
  await expect(drawer.getByText('本卷当前没有需要模型处理的候选空位')).toBeVisible()
  const adoptedInDrawer = drawer.locator('.ant-card').filter({ hasText: '答案建议采用记录' })
  await expect(adoptedInDrawer.getByText('采用 2 空 · 评分修订 v2')).toBeVisible()
  await drawer.getByRole('button', { name: '关闭', exact: true }).click()
  const adoptedOnPage = page.locator('.attempt-result .ant-card').filter({ hasText: '答案建议采用记录' })
  await expect(adoptedOnPage.getByText('第 1 小题 · 第 1 空', { exact: true })).toBeVisible()
  await expect(adoptedOnPage.getByText('第 2 小题 · 第 1 空', { exact: true })).toBeVisible()
  await expect(adoptedOnPage.getByText('第 3 小题 · 第 1 空', { exact: true })).toHaveCount(0)
  await page.reload()
  await expect(adoptedOnPage.getByText('本空可接受', { exact: true })).toHaveCount(2)
  await expect(page.getByText('+30', { exact: true }).first()).toBeVisible()
  expect(observed.unexpected).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy()
})

test('persisted adoption identifies exact blanks and both verdicts without another model request', async ({ page }) => {
  const observed = await mock(page, 'adopted_mixed')
  await page.goto(`/paper-instances/${paper}/grading`)
  const adoption = page.locator('.attempt-result .ant-card').filter({ hasText: '答案建议采用记录' })
  await expect(adoption.getByText('已采用建议', { exact: true })).toBeVisible()
  const leaves = adoption.locator('.ant-space-item > .ant-space')
  await expect(adoption.getByText('第 1 小题 · 第 2 空', { exact: true })).toBeVisible()
  await expect(adoption.getByText('第 2 小题 · 第 4 空', { exact: true })).toBeVisible()
  await expect(adoption.getByText('第 2 小题 · 第 5 空', { exact: true })).toHaveCount(0)
  await expect(leaves.filter({ hasText: '第 1 小题 · 第 2 空' }).getByText('本空可接受', { exact: true })).toBeVisible()
  await expect(leaves.filter({ hasText: '第 2 小题 · 第 4 空' }).getByText('本空不可接受', { exact: true })).toBeVisible()
  await expect(adoption.getByText('核对人：测试教师')).toBeVisible()
  await page.getByRole('button', { name: '整卷等价判断', exact: true }).click()
  const drawer = page.getByRole('dialog', { name: '整卷等价判断', exact: true })
  await expect(drawer.getByText('第 2 小题 · 第 4 空', { exact: true })).toBeVisible()
  expect(observed.adoptions).toEqual([])
  expect(observed.schedules).toEqual([])
  expect(observed.unexpected).toEqual([])
  expect(await drawer.evaluate(e => e.scrollWidth <= e.clientWidth + 1)).toBeTruthy()
})

test('later grading revision labels retained adoption as history', async ({ page }) => {
  const observed = await mock(page, 'historical')
  await page.goto(`/paper-instances/${paper}/grading`)
  const adoption = page.locator('.attempt-result .ant-card').filter({ hasText: '答案建议采用记录' })
  await expect(adoption.getByText('历史采用记录', { exact: true })).toBeVisible()
  await expect(adoption.getByText('已采用建议', { exact: true })).toHaveCount(0)
  await expect(adoption.getByText('当前评分以最新修订为准', { exact: false })).toBeVisible()
  await expect(adoption.getByText('采用 2 空 · 评分修订 v2')).toBeVisible()
  await expect(page.getByText('待教师复核', { exact: true })).toBeVisible()
  expect(observed.adoptions).toEqual([])
  expect(observed.schedules).toEqual([])
})

for (const mode of ['fake', 'uncertain'] as const) test(`${mode} results cannot update grading`, async ({ page }) => {
  const observed = await mock(page, mode)
  const drawer = await open(page)
  await expect(drawer.getByRole('button', { name: '采用第 3 题建议' })).toBeDisabled()
  await expect(drawer.getByText('建议：无法确定', { exact: false })).toHaveCount(2)
  await expect(page.getByText('已采用建议', { exact: true })).toHaveCount(0)
  expect(observed.adoptions).toEqual([])
  expect(observed.schedules).toEqual([])
})

test('stale scan clears action state and never silently retries paid dispatch', async ({ page }) => {
  const observed = await mock(page, 'stale')
  const drawer = await open(page)
  await drawer.getByRole('button', { name: '统一发起等价判断（调用模型）' }).click()
  await page.getByRole('dialog', { name: '确认统一发起等价判断' }).getByRole('button', { name: '确认发起', exact: true }).click()
  await expect(drawer.getByText('作答已变化，请重新检查整卷')).toBeVisible()
  await expect(drawer.getByRole('button', { name: '统一发起等价判断（调用模型）' })).toHaveCount(0)
  expect(observed.schedules).toHaveLength(1)
  expect(observed.adoptions).toEqual([])
})

test('adoption explains the exact blocking leaf and missing audit labels after reload', async ({ page }) => {
  const observed = await mock(page, 'mixed')
  const drawer = await open(page)
  await expect(drawer.getByRole('button', { name: '采用第 3 题建议' })).toBeDisabled()
  const warning = drawer.getByRole('alert').filter({ hasText: '本题暂不能采用建议' })
  await expect(warning.getByText('第 2 小题 · 第 1 空：模型返回“无法确定”，请使用本题原有人工复核')).toBeVisible()
  await expect(warning.getByText('第 1 小题', { exact: false })).toHaveCount(0)
  await expect(drawer.getByText('建议：本空可接受', { exact: false })).toHaveCount(1)
  await drawer.getByLabel('整卷判断核对人').fill('')
  await expect(warning.getByText('请填写核对人及至少两个字的原因')).toBeVisible()
  await drawer.getByLabel('整卷判断核对人').fill('测试教师')
  await expect(warning.getByText('请填写核对人及至少两个字的原因')).toHaveCount(0)
  expect(observed.schedules).toEqual([])
  expect(observed.adoptions).toEqual([])
})

test('incomplete question context is explained without scheduling model tasks', async ({ page }) => {
  const observed = await mock(page, 'context_missing')
  await page.goto(`/paper-instances/${paper}/grading`)
  await page.getByRole('button', { name: '整卷等价判断', exact: true }).click()
  const drawer = page.getByRole('dialog', { name: '整卷等价判断', exact: true })
  await drawer.getByRole('button', { name: '检查或刷新整卷' }).click()
  await drawer.getByText('查看跳过的空位与原因').click()
  await expect(drawer.getByText('本空上下文包含未文本化的题图或公式', { exact: false })).toHaveCount(2)
  await expect(drawer.getByRole('button', { name: '统一发起等价判断（调用模型）' })).toBeDisabled()
  expect(observed.schedules).toEqual([])
})
