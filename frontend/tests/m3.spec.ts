import { expect, test } from '@playwright/test'

const api = process.env.M2_API_URL ?? 'http://localhost:8000/api/v1'

async function noOverflow(page: import('@playwright/test').Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy()
}

async function configurePersonalizedFilter(page: import('@playwright/test').Page) {
  await page.getByRole('button', { name: /个性化/ }).click()
  const knowledgePoint = page.getByRole('combobox', { name: '节（知识点，必选）' })
  await knowledgePoint.click()
  await knowledgePoint.press('ArrowDown')
  await knowledgePoint.press('Enter')
  await knowledgePoint.press('Escape')
  const competency = page.getByRole('combobox', { name: '主要能力（任选其一）' })
  await expect(competency).toBeEnabled()
  await competency.click()
  await competency.press('ArrowDown')
  await competency.press('Enter')
}

test('assignment navigation and multi-student preview stay read-only', async ({ page }, testInfo) => {
  const before = await (await page.request.get(`${api}/assignments?page_size=100`)).json()
  await page.goto('/assignments')
  await expect(page.getByRole('heading', { name: '个性化作业', exact: true })).toBeVisible()
  await page.getByRole('link', { name: '创建作业', exact: true }).click()
  await expect(page.getByRole('heading', { name: '创建班级作业', exact: true })).toBeVisible()
  await expect(page.getByText(/当前班级全部学生/)).toBeVisible()
  await configurePersonalizedFilter(page)
  await page.getByRole('button', { name: '预检全部学生' }).click()
  await expect(page.getByText(/名学生可完整生成/)).toBeVisible()
  await expect(page.getByText('预检不写入数据库', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '生成草稿' })).toBeVisible()
  await noOverflow(page)
  await page.screenshot({ path: testInfo.outputPath('assignment-preview.png'), fullPage: true })

  const after = await (await page.request.get(`${api}/assignments?page_size=100`)).json()
  expect(after.total).toBe(before.total)
})

test('assignment creation page has no global overflow on narrow viewport', async ({ page }) => {
  await page.goto('/assignments/new')
  await expect(page.getByRole('heading', { name: '创建班级作业', exact: true })).toBeVisible()
  await noOverflow(page)
})

test('assignment creation sends large classes as a server-expanded scope', async ({ page }) => {
  const classId = '00000000-0000-4000-8000-000000000099'
  const requestedPages: number[] = []
  let previewRequest: Record<string, unknown> | null = null
  await page.route('**/api/v1/classes', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify([{
      id: classId, name: '大班测试', grade: '六年级', school_year: '2026-2027',
      teacher_name: null, student_count: 101, created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
    }]),
  }))
  await page.route('**/api/v1/students?**', (route) => {
    const url = new URL(route.request().url())
    const current = Number(url.searchParams.get('page') || 1)
    requestedPages.push(current)
    const start = (current - 1) * 100
    const items = Array.from({ length: current === 1 ? 100 : 1 }, (_, index) => {
      const number = start + index + 1
      return {
        id: `00000000-0000-4000-8000-${String(number).padStart(12, '0')}`,
        student_code: `T${number}`, display_name: `学生${number}`, class_id: classId,
        class_name: '大班测试', is_demo: false, attempt_count: 0, correct_count: 0, latest_answered_at: null,
      }
    })
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items, total: 101, page: current, page_size: 100 }),
    })
  })
  await page.route('**/api/v1/assignments/preview', async (route) => {
    previewRequest = route.request().postDataJSON() as Record<string, unknown>
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        as_of: new Date().toISOString(), student_count: 101, ready_count: 101,
        shortfall_student_count: 0, students: [],
      }),
    })
  })

  await page.goto('/assignments/new')
  await expect(page.getByText('当前班级全部学生（101 人）', { exact: true })).toBeVisible()
  await configurePersonalizedFilter(page)
  await page.getByRole('button', { name: '预检全部学生' }).click()
  await expect(page.getByText('101 / 101 名学生可完整生成')).toBeVisible()
  expect(requestedPages).toEqual([])
  expect(previewRequest).toMatchObject({
    class_id: classId,
    student_scope: 'all_class',
    student_ids: [],
  })
})

test('required filters use catalog grades and required-only inventory', async ({ page }) => {
  const classId = '00000000-0000-4000-8000-000000000088'
  const knowledgePointId = '00000000-0000-4000-8000-000000000089'
  const historyAssignmentId = '00000000-0000-4000-8000-000000000090'
  const foreignHistoryAssignmentId = '00000000-0000-4000-8000-000000000091'
  let previewRequest: Record<string, any> | null = null
  await page.route('**/api/v1/classes', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify([{
      id: classId, name: '六年级测试班', grade: '六年级', school_year: '2026-2027',
      teacher_name: null, student_count: 1, created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
    }]),
  }))
  await page.route('**/api/v1/questions/catalog', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify([{
      code: 'math.g6.test', name: '测试章', sections: ['测试节'], grades: ['六年级上册'],
      core_literacies: ['运算能力'],
      knowledge_points: [{
        code: 'math.g6.test.kp.01', name: '测试知识点', section: '测试节',
        mastery_level: '掌握', core_competencies: ['运算能力'],
      }],
    }]),
  }))
  await page.route('**/api/v1/knowledge-points?**', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify([{
      id: knowledgePointId, code: 'math.g6.test.kp.01', name: '测试知识点',
      chapter_code: 'math.g6.test', available_question_count: 5,
      required_available_question_count: 2,
    }]),
  }))
  await page.route('**/api/v1/assignments/recent-completed?**', (route) => {
    expect(new URL(route.request().url()).searchParams.get('class_id')).toBe(classId)
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([{
        id: historyAssignmentId, title: '历史来源作业', class_id: classId,
        class_name: '六年级测试班', chapter_code: 'math.g6.history',
        assignment_type: 'practice', student_count: 1, draft_count: 0,
        published_count: 1, invalidated_count: 0, created_at: new Date().toISOString(),
      }, {
        id: foreignHistoryAssignmentId, title: '其他班级作业',
        class_id: '00000000-0000-4000-8000-000000000099',
        class_name: '其他班级', chapter_code: 'math.g6.history',
        assignment_type: 'practice', student_count: 1, draft_count: 0,
        published_count: 1, invalidated_count: 0, created_at: new Date().toISOString(),
      }]),
    })
  })
  await page.route('**/api/v1/assignments/preview', async (route) => {
    previewRequest = route.request().postDataJSON() as Record<string, any>
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        as_of: new Date().toISOString(), student_count: 1, ready_count: 1,
        shortfall_student_count: 0, students: [],
      }),
    })
  })

  await page.goto('/assignments/new')
  await page.getByRole('button', { name: '预检全部学生' }).click()
  const previewActionBox = await page.locator('.assignment-preview-action').boundingBox()
  const errorAlert = page.locator('.ant-alert-error')
  await expect(errorAlert).toBeVisible()
  const errorBox = await errorAlert.boundingBox()
  expect(errorBox!.y).toBeGreaterThanOrEqual(previewActionBox!.y + previewActionBox!.height)
  const grade = page.getByRole('combobox', { name: '年级（可多选）' })
  await grade.click()
  const gradeDropdown = page.locator('.ant-select-dropdown:visible')
  await expect(
    gradeDropdown.locator('.ant-select-item-option-content').filter({ hasText: /^六年级上册$/ }),
  ).toBeVisible()
  await expect(
    gradeDropdown.locator('.ant-select-item-option-content').filter({ hasText: /^六年级$/ }),
  ).toHaveCount(0)
  await grade.press('Escape')

  const requiredKnowledge = page.getByRole('combobox', { name: '节（知识点，可选）' })
  await requiredKnowledge.click()
  await expect(
    page.locator('.ant-select-dropdown:visible .ant-select-item-option-content')
      .filter({ hasText: /^测试知识点（2题）$/ }),
  ).toBeVisible()
  await requiredKnowledge.press('Escape')

  const twoStar = page.getByRole('spinbutton', { name: '2 星' })
  await twoStar.click()
  await twoStar.press('Control+A')
  await twoStar.press('1')
  await twoStar.press('Tab')
  await expect(twoStar).toHaveValue('1')
  await configurePersonalizedFilter(page)
  await page.getByRole('button', { name: /历史回顾/ }).click()
  const historyAssignment = page.getByRole('combobox', { name: '当前班级最近 5 次已批改作业' })
  await historyAssignment.click()
  const historyDropdown = page.locator('.ant-select-dropdown:visible')
  await expect(historyDropdown.getByText('其他班级作业', { exact: false })).toHaveCount(0)
  await historyAssignment.press('ArrowDown')
  await historyAssignment.press('Enter')
  await historyAssignment.press('Escape')
  await page.getByRole('button', { name: '预检全部学生' }).click()
  await expect.poll(() => previewRequest).not.toBeNull()
  expect(previewRequest).toMatchObject({
    recommendation_parameters: {
      grade_codes: ['六年级上册'],
      chapter_codes: ['math.g6.test'],
      new_count: 1,
    },
    filter_config: {
      grade_codes: ['六年级上册'],
      chapter_codes: ['math.g6.test'],
      required: { difficulty_counts: { '1': 0, '2': 1, '3': 0, '4': 0, '5': 0 } },
      personalized: {
        knowledge_point_codes: ['math.g6.test.kp.01'],
        primary_competency: '运算能力',
        question_count: 3,
      },
      history: { assignment_id: historyAssignmentId },
    },
  })
})
