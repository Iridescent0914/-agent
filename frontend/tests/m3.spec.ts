import { expect, test } from '@playwright/test'

const api = process.env.M2_API_URL ?? 'http://localhost:8000/api/v1'

async function noOverflow(page: import('@playwright/test').Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy()
}

test('assignment navigation and multi-student preview stay read-only', async ({ page }, testInfo) => {
  const before = await (await page.request.get(`${api}/assignments?page_size=100`)).json()
  await page.goto('/assignments')
  await expect(page.getByRole('heading', { name: '个性化作业', exact: true })).toBeVisible()
  await page.getByRole('link', { name: '创建作业', exact: true }).click()
  await expect(page.getByRole('heading', { name: '创建个性化作业', exact: true })).toBeVisible()
  await expect(page.getByRole('radio', { name: '全班学生' })).toBeChecked()

  const catalog = await (await page.request.get(`${api}/exam-points?chapter_code=math.g6.rational`)).json()
  const sufficient = catalog.find((item: { available_question_count: number }) => item.available_question_count >= 6)
  await page.getByLabel('回顾范围').click()
  await page.getByRole('combobox', { name: '回顾范围' }).fill(sufficient.name)
  await page.locator('.ant-select-dropdown:visible').getByText(`${sufficient.name}（${sufficient.available_question_count}题）`, { exact: true }).click()
  await page.getByRole('spinbutton', { name: '单考点最大占比' }).fill('1')
  await page.getByRole('spinbutton', { name: '避免近期重复天数' }).fill('0')
  await page.getByRole('button', { name: '预检全部学生' }).click()
  await expect(page.getByText(/名学生可完整生成/)).toBeVisible()
  await expect(page.getByText('预检不会写入作业', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '生成草稿' })).toBeVisible()
  await noOverflow(page)
  await page.screenshot({ path: testInfo.outputPath('assignment-preview.png'), fullPage: true })

  const after = await (await page.request.get(`${api}/assignments?page_size=100`)).json()
  expect(after.total).toBe(before.total)
})

test('assignment creation page has no global overflow on narrow viewport', async ({ page }) => {
  await page.goto('/assignments/new')
  await expect(page.getByRole('heading', { name: '创建个性化作业', exact: true })).toBeVisible()
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
  await expect(page.getByText('学生（当前班级 101 人）', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '预检全部学生' }).click()
  await expect(page.getByText('101 / 101 名学生可完整生成')).toBeVisible()
  expect(requestedPages).toEqual([])
  expect(previewRequest).toMatchObject({
    class_id: classId,
    student_scope: 'all_class',
    student_ids: [],
  })
})
