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
