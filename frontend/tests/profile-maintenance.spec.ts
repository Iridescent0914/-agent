import { expect, test } from '@playwright/test'

const api = process.env.M2_API_URL ?? 'http://localhost:8000/api/v1'

test('student table distinguishes confirmed evidence, pending answers and history', async ({ page }) => {
  await page.route('**/api/v1/**', async (route) => {
    const response = await page.request.fetch(route.request())
    await route.fulfill({ response })
  })
  const listing = await (await page.request.get(`${api}/students?page_size=100`)).json()
  const student = listing.items[0]
  expect(student).toBeTruthy()
  const profile = await (await page.request.get(`${api}/students/${student.id}/profile`)).json()
  expect(student.attempt_count).toBe(profile.summary.valid_attempt_count)
  expect(student.total_attempt_count).toBe(profile.summary.total_attempt_count)
  expect(student.pending_review_count).toBe(profile.summary.pending_review_count)
  await page.goto('/students')
  await expect(page.getByRole('columnheader', { name: '有效确认作答' })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: '有效确认正确率' })).toBeVisible()
  const row = page.getByRole('row').filter({ has: page.getByRole('link', { name: student.student_code, exact: true }) })
  await expect(row.getByRole('cell').nth(3)).toHaveText(`${student.attempt_count} 次`)
  await expect(row.getByRole('cell').nth(5)).toHaveText(String(student.pending_review_count))
  await expect(row.getByRole('cell').nth(6)).toHaveText(String(student.total_attempt_count))
})
