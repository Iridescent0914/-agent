import { expect, test, type Page } from '@playwright/test'


async function noOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy()
}


test('class analytics explains evidence and drills down to a student', async ({ page }) => {
  await page.goto('/analytics')
  await expect(page.getByRole('heading', { name: '班级学情', exact: true })).toBeVisible()
  await expect(page.getByText('在册学生', { exact: true })).toBeVisible()
  await expect(page.getByText('确认作答', { exact: true })).toBeVisible()
  await expect(page.getByText('正确率只统计有效确认作答', { exact: false })).toBeVisible()
  await expect(page.getByRole('img', { name: '班级知识点正确率条形图' })).toBeVisible()
  await expect(page.getByRole('table').first()).toBeVisible()
  await noOverflow(page)

  const analyticsCard = page.locator('.ant-card').filter({ has: page.getByText('知识点学情', { exact: true }) })
  await analyticsCard.getByRole('button').filter({ hasNotText: /正确率|掌握度/ }).first().click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.getByText('未评估不是 0 分', { exact: false })).toBeVisible()
  const studentLink = page.getByRole('dialog').getByRole('link').first()
  await expect(studentLink).toBeVisible()
  await studentLink.click()
  await expect(page.getByRole('heading', { level: 3 })).toBeVisible()
  await noOverflow(page)
})


test('analytics metric can switch and page remains usable on narrow screens', async ({ page }) => {
  await page.goto('/')
  await expect(page).toHaveURL(/\/analytics/)
  await page.getByText('掌握度参考值', { exact: true }).click()
  await expect(page.getByRole('img', { name: '班级知识点掌握度参考均值条形图' })).toBeVisible()
  await noOverflow(page)
})


test('analytics request failure exposes a retry action', async ({ page }) => {
  let failed = false
  await page.route('**/api/v1/analytics/class-overview**', async (route) => {
    if (!failed) {
      failed = true
      await route.fulfill({ status: 503, contentType: 'application/json', body: '{"message":"验收模拟请求失败"}' })
    } else {
      await route.continue()
    }
  })
  await page.goto('/analytics')
  await expect(page.getByText('学情加载失败', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: /重\s*试/ }).click()
  await expect(page.getByText('在册学生', { exact: true })).toBeVisible()
})
