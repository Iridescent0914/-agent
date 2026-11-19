import { expect, test, type Page } from '@playwright/test'

const studentId = '00000000-0000-4000-8000-000000000151'

async function workspace(page: Page, mode: 'confirmed' | 'empty' | 'wrong' | 'legacy' = 'confirmed') {
  const historical = { scope: 'all_history', counting_unit: 'attempt_question_group',
    confirmed_attempt_count: mode === 'empty' ? 0 : 11,
    correct_count: mode === 'empty' || mode === 'wrong' ? 0 : 9,
    incorrect_count: mode === 'empty' ? 0 : mode === 'wrong' ? 11 : 2,
    distinct_question_count: mode === 'empty' ? 0 : 8, pending_review_count: 3, excluded_attempt_count: 2,
    accuracy: mode === 'empty' ? null : mode === 'wrong' ? 0 : 9 / 11 }
  const points = [1, 2].map(n => ({ id: `kp-${n}`, code: `chapter.${n}.kp`, name: `知识点${n}`,
    chapter_code: `chapter.${n}`, chapter_name: `第${n}章`, grade: '六年级', core_competencies: [],
    available_question_count: 10, required_available_question_count: 0 }))
  const profile = { student_id: studentId, computed_at: '2026-11-19T00:00:00Z', rule_version: 'm2-v1',
    summary: { total_attempt_count: 16, valid_attempt_count: 11, pending_review_count: 3,
      invalid_evidence_count: 2, distinct_question_count: 8, assessed_knowledge_point_count: 2, unassessed_knowledge_point_count: 0 },
    ...(mode === 'legacy' ? {} : { historical_accuracy: historical }),
    masteries: points.map((p, i) => ({ knowledge_point_id: p.id, knowledge_point_code: p.code,
      knowledge_point_name: p.name, assessment_status: 'assessed', mastery: .5, learning_status: 'yes', confidence_level: 'medium',
      attempt_count: i === 0 ? 10 : 1, correct_count: i === 0 ? 9 : 0, distinct_question_count: 4,
      recent_attempt_count: 1, recent_wrong_count: 0, available_question_count: 10 })),
    literacies: [], focus_points: [], recent_errors: [], excluded_evidence: [], evidence_note: '' }
  const student = { id: studentId, student_code: 'ACC001', display_name: '累计统计样例',
    class_name: '统计班', class_id: 'class-1', is_demo: false }
  const unexpected: string[] = [], chapters: string[] = []
  await page.route('**/api/v1/**', async route => {
    const req = route.request(), url = new URL(req.url())
    if (req.method() !== 'GET') {
      unexpected.push(`${req.method()} ${url.pathname}`)
      return route.fulfill({ status: 500, json: { message: 'unexpected mutation' } })
    }
    if (url.pathname.endsWith(`/students/${studentId}/profile`)) return route.fulfill({ json: profile })
    if (url.pathname.endsWith(`/students/${studentId}`)) return route.fulfill({ json: student })
    if (url.pathname.endsWith('/students')) return route.fulfill({ json: { items: [student], total: 1, page: 1, page_size: 50 } })
    if (url.pathname.endsWith('/knowledge-points')) return route.fulfill({ json: points })
    if (url.pathname.endsWith('/questions/catalog')) return route.fulfill({ json: points.map((p, i) => ({
      code: p.chapter_code, name: `第${i === 0 ? '一' : '二'}章`, grades: ['六年级'], knowledge_points: [p],
    })) })
    if (url.pathname.endsWith('/analytics/class-overview')) {
      chapters.push(url.searchParams.get('chapter_code') ?? '')
      return route.fulfill({ json: { knowledge_points: [], literacies: [] } })
    }
    unexpected.push(`${req.method()} ${url.pathname}`)
    return route.fulfill({ status: 500, json: { message: 'unexpected request' } })
  })
  await page.goto(`/students/${studentId}`)
  await expect(page.getByText('累计统计样例', { exact: true })).toBeVisible()
  return { unexpected, chapters }
}

test('historical accuracy keeps all chapters while knowledge-point chapter changes', async ({ page }, testInfo) => {
  const mock = await workspace(page)
  const card = page.getByLabel('历史累计正确率统计', { exact: true })
  await expect(card).toContainText('81.8')
  await expect(card).toContainText('共 8 道不同题；待复核 3 题、失效或无效记录 2 条不计入正确率。')
  await page.locator('.ant-select').filter({ has: page.getByRole('combobox', { name: '选择画像章节', exact: true }) }).click()
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').getByText('第二章', { exact: true }).click()
  await expect.poll(() => mock.chapters.includes('chapter.2')).toBeTruthy()
  await expect(card).toContainText('81.8')
  await expect(card).toContainText('累计已确认题数')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy()
  await card.screenshot({ path: testInfo.outputPath('historical-accuracy.png') })
  expect(mock.unexpected).toEqual([])
})

test('unconfirmed history has no percentage while confirmed wrong history displays zero', async ({ page }) => {
  let mock = await workspace(page, 'empty')
  const card = page.getByLabel('历史累计正确率统计', { exact: true })
  await expect(card).toContainText('暂无已确认作答，暂不计算正确率。')
  await expect(card.locator('.ant-statistic-content-suffix')).toHaveCount(0)
  expect(mock.unexpected).toEqual([])
  await page.unroute('**/api/v1/**')
  mock = await workspace(page, 'wrong')
  await expect(card.locator('.ant-statistic').first()).toContainText('0.0')
  await expect(card.locator('.ant-statistic-content-suffix')).toHaveText('%')
  expect(mock.unexpected).toEqual([])
})

test('older profile response never invents zero accuracy', async ({ page }) => {
  const mock = await workspace(page, 'legacy')
  const card = page.getByLabel('历史累计正确率统计', { exact: true })
  await expect(card).toContainText('累计统计暂不可用，请刷新后重试。')
  await expect(card.locator('.ant-statistic')).toHaveCount(0)
  expect(mock.unexpected).toEqual([])
})
