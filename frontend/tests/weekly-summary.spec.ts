import { expect, test } from '@playwright/test'

test('assignment list exposes the independent weekly summary workflow', async ({ page }) => {
  await page.goto('/assignments')
  await expect(page.getByRole('heading', { name: '个性化作业' })).toBeVisible()
  const entry = page.getByRole('link', { name: '创建周总结' })
  await expect(entry).toBeVisible()
  await entry.click()
  await expect(page).toHaveURL(/\/assignments\/weekly-summary\/new/)
  await expect(page.getByRole('heading', { name: '创建周总结' })).toBeVisible()
  await expect(page.getByText('周总结是独立于当天作业的第二套卷子')).toBeVisible()
  await expect(page.getByLabel('周总结标题')).toBeVisible()
  await expect(page.getByLabel('班级')).toBeVisible()
  await expect(page.getByText('来源作业（最近六次，可多选）')).toBeVisible()
  await expect(page.getByRole('button', { name: /预\s*检/ })).toBeVisible()
  await expect(page.getByRole('button', { name: /生成周总结草稿/ })).toBeVisible()
})

test('weekly summary explains why no student drafts are generated', async ({ page }) => {
  const classId = '10000000-0000-0000-0000-000000000001'
  const sourceId = '20000000-0000-0000-0000-000000000001'
  const assignmentId = '30000000-0000-0000-0000-000000000001'
  const createdAt = '2026-10-29T08:00:00Z'
  const assignment = {
    id: assignmentId,
    title: '无人需要的周总结',
    class_id: classId,
    class_name: '六年级测试班',
    chapter_code: 'weekly-summary',
    assignment_type: 'weekly_summary',
    student_count: 0,
    draft_count: 0,
    published_count: 0,
    invalidated_count: 0,
    created_at: createdAt,
    teaching_scope: {},
    generation_config: {},
    papers: [],
  }

  await page.route('**/api/v1/classes', (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify([{ id: classId, name: '六年级测试班', student_count: 2 }]),
  }))
  await page.route('**/api/v1/assignments/recent-completed?**', (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify([{
      id: sourceId,
      title: '来源作业一',
      class_id: classId,
      class_name: '六年级测试班',
      chapter_code: 'chapter-1',
      assignment_type: 'practice',
      student_count: 2,
      draft_count: 0,
      published_count: 2,
      invalidated_count: 0,
      created_at: createdAt,
    }]),
  }))
  await page.route('**/api/v1/assignments/weekly-summary/preview', (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({
      as_of: createdAt,
      student_count: 2,
      paper_student_count: 0,
      ready_count: 0,
      shortfall_student_count: 0,
      source_assignment_ids: [sourceId],
      involved_knowledge_point_codes: [],
      students: [
        { student_id: '40000000-0000-0000-0000-000000000001', student_code: 'S001', student_name: '学生一', false_knowledge_points: [], requested_count: 0, selected_count: 0, shortfall: 0, status: 'no_review_needed', items: [] },
        { student_id: '40000000-0000-0000-0000-000000000002', student_code: 'S002', student_name: '学生二', false_knowledge_points: [], requested_count: 0, selected_count: 0, shortfall: 0, status: 'no_review_needed', items: [] },
      ],
    }),
  }))
  await page.route('**/api/v1/assignments/weekly-summary', (route) => route.fulfill({
    status: 201,
    contentType: 'application/json',
    body: JSON.stringify(assignment),
  }))
  await page.route(`**/api/v1/assignments/${assignmentId}?include_papers=false`, (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify(assignment),
  }))
  await page.route(`**/api/v1/assignments/${assignmentId}/papers?**`, (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({ items: [], total: 0, page: 1, page_size: 50 }),
  }))

  await page.goto('/assignments/weekly-summary/new')
  await page.getByLabel('来源作业（最近六次，可多选）').click()
  await page.locator('.ant-select-dropdown:visible .ant-select-item-option', { hasText: '来源作业一' }).click()
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: /预\s*检/ }).click()
  await expect(page.getByText('本次无需生成学生草稿')).toBeVisible()

  await page.getByRole('button', { name: /生成周总结草稿/ }).click()
  await expect(page.getByText('周总结记录已创建，但全班暂无标“否”的知识点，本次未生成学生草稿。')).toBeVisible()
  await expect(page).toHaveURL(new RegExp(`/assignments/${assignmentId}$`))
  await expect(page.getByText('本次没有需要生成周总结的学生')).toBeVisible()
})
