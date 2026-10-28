import { expect, test } from '@playwright/test'

const classA = '00000000-0000-4000-8000-0000000000a1'
const classB = '00000000-0000-4000-8000-0000000000b1'

const classes = [
  {
    id: classA, name: '六年级一班', grade: '六年级', school_year: '2026-2027',
    teacher_name: null, student_count: 2,
    created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
  },
  {
    id: classB, name: '六年级二班', grade: '六年级', school_year: '2026-2027',
    teacher_name: null, student_count: 1,
    created_at: '2026-10-01T00:00:00Z', updated_at: '2026-10-01T00:00:00Z',
  },
]

test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/classes', (route) => route.fulfill({ json: classes }))
})

test('assignment list is isolated by the selected class', async ({ page }) => {
  const requestedClassIds: string[] = []
  await page.route('**/api/v1/assignments?**', (route) => {
    const url = new URL(route.request().url())
    const classId = url.searchParams.get('class_id') ?? ''
    requestedClassIds.push(classId)
    const firstClass = classId === classA
    return route.fulfill({
      json: {
        items: [{
          id: firstClass ? '00000000-0000-4000-8000-0000000000a2' : '00000000-0000-4000-8000-0000000000b2',
          title: firstClass ? '一班作业' : '二班作业',
          class_id: firstClass ? classA : classB,
          class_name: firstClass ? '六年级一班' : '六年级二班',
          chapter_code: 'math.g6.rational', assignment_type: 'practice',
          student_count: firstClass ? 2 : 1, draft_count: 1,
          published_count: 0, invalidated_count: 0,
          created_at: '2026-10-28T00:00:00Z',
        }],
        total: 1, page: 1, page_size: 20,
      },
    })
  })

  await page.goto('/assignments')
  await expect(page.getByText('一班作业', { exact: true })).toBeVisible()
  await expect(page.getByText('二班作业', { exact: true })).toHaveCount(0)
  expect(requestedClassIds.at(-1)).toBe(classA)

  await page.locator('.ant-select').filter({
    has: page.locator('input[aria-label="班级"]'),
  }).click()
  await page.getByText('六年级二班（1人）', { exact: true }).click()
  await expect(page.getByText('二班作业', { exact: true })).toBeVisible()
  await expect(page.getByText('一班作业', { exact: true })).toHaveCount(0)
  expect(requestedClassIds.at(-1)).toBe(classB)
  await expect(page).toHaveURL(new RegExp(`class_id=${classB}`))
})

test('grading queue is isolated by the selected class', async ({ page }) => {
  const requestedClassIds: string[] = []
  await page.route('**/api/v1/grading/queue?**', (route) => {
    const url = new URL(route.request().url())
    const classId = url.searchParams.get('class_id') ?? ''
    requestedClassIds.push(classId)
    const firstClass = classId === classA
    return route.fulfill({
      json: {
        items: [{
          paper_instance_id: firstClass ? '00000000-0000-4000-8000-0000000000a3' : '00000000-0000-4000-8000-0000000000b3',
          assignment_id: firstClass ? '00000000-0000-4000-8000-0000000000a2' : '00000000-0000-4000-8000-0000000000b2',
          assignment_title: firstClass ? '一班待批改作业' : '二班待批改作业',
          class_id: firstClass ? classA : classB,
          class_name: firstClass ? '六年级一班' : '六年级二班',
          student_id: firstClass ? '00000000-0000-4000-8000-0000000000a4' : '00000000-0000-4000-8000-0000000000b4',
          student_code: firstClass ? 'A001' : 'B001',
          student_name: firstClass ? '一班学生' : '二班学生',
          version_no: 1, instance_code: firstClass ? 'CLASS-A' : 'CLASS-B',
          is_active: true, invalidation_type: null,
          grading_status: 'unsubmitted', item_count: 6,
          needs_review_count: 0, submitted_at: null,
        }],
        total: 1,
      },
    })
  })

  await page.goto('/grading')
  await expect(page.getByText('一班待批改作业', { exact: true })).toBeVisible()
  await expect(page.getByText('二班待批改作业', { exact: true })).toHaveCount(0)
  expect(requestedClassIds.at(-1)).toBe(classA)

  await page.locator('.ant-select').filter({
    has: page.locator('input[aria-label="班级"]'),
  }).click()
  await page.getByText('六年级二班（1人）', { exact: true }).click()
  await expect(page.getByText('二班待批改作业', { exact: true })).toBeVisible()
  await expect(page.getByText('一班待批改作业', { exact: true })).toHaveCount(0)
  expect(requestedClassIds.at(-1)).toBe(classB)
  await expect(page).toHaveURL(new RegExp(`class_id=${classB}`))
})
