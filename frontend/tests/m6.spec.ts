import { expect, test, type Page } from '@playwright/test'

const paperId = '00000000-0000-4000-8000-000000000061'
const runId = '00000000-0000-4000-8000-000000000062'

async function noOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy()
}

test('student print view keeps the instance code and exposes no answer key', async ({ page }) => {
  await page.route(`**/api/v1/paper-instances/${paperId}/print-view`, (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({
      paper_instance_id: paperId,
      assignment_title: 'M6 有理数练习',
      student_code: 'S001',
      version_no: 1,
      instance_code: 'RAT-M600-0001',
      layout_version: 'm6-print-v1',
      is_active: true,
      items: [{
        id: '00000000-0000-4000-8000-000000000063', order_no: 1,
        question_snapshot: { question_text: '计算：-1 + 2 = ____', question_type: 'fill_blank' },
        options_snapshot: null, assets: [],
      }],
      answer_slots: [{ order_no: 1, blank_ids: ['answer'], kind: 'fill_blank' }],
    }),
  }))
  await page.goto(`/paper-instances/${paperId}/print`)
  await expect(page.getByRole('heading', { name: 'M6 有理数练习' })).toBeVisible()
  await expect(page.getByText('RAT-M600-0001', { exact: false }).first()).toBeVisible()
  await expect(page.getByText('统一答题区', { exact: false })).toBeVisible()
  await expect(page.getByText('标准答案', { exact: false })).toHaveCount(0)
  await noOverflow(page)
})

test('image upload shows uncertainty before committing to review', async ({ page }) => {
  await page.route(`**/api/v1/paper-instances/${paperId}/submission`, (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({
      submission_id: null, paper_instance_id: paperId,
      assignment_id: '00000000-0000-4000-8000-000000000064',
      assignment_title: 'M6 有理数练习', instance_code: 'RAT-M600-0001', version_no: 1,
      paper_status: 'published', is_active: true, can_submit: true,
      student_id: '00000000-0000-4000-8000-000000000065', student_code: 'S001', student_name: '学生一',
      source: null, submitted_at: null, replayed: false, unchanged: false,
      answer_key_update_count: 0, regraded_count: 0,
      summary: { total_count: 0, correct_count: 0, incorrect_count: 0, needs_review_count: 0 },
      items: [], mastery_update_stats: null,
    }),
  }))
  const baseRun = {
    id: runId, paper_instance_id: paperId,
    student_id: '00000000-0000-4000-8000-000000000065', submission_id: null,
    provider: 'fake', model: 'fake-m6-v1', prompt_version: 'm6-answer-reader-v1',
    original_filename: 'answers.jpg', content_type: 'image/jpeg', byte_size: 4,
    pixel_width: 10, pixel_height: 10, error_code: null, error_message: null,
    created_at: new Date().toISOString(), completed_at: null,
    image_url: `/api/v1/recognition-runs/${runId}/image`, items: [],
  }
  await page.route(`**/api/v1/paper-instances/${paperId}/recognition-runs`, (route) => route.fulfill({
    status: 201, contentType: 'application/json', body: JSON.stringify({ ...baseRun, status: 'uploaded' }),
  }))
  await page.route(`**/api/v1/recognition-runs/${runId}/extract`, (route) => route.fulfill({
    contentType: 'application/json', body: JSON.stringify({
      ...baseRun, status: 'ready_for_review', items: [{
        id: '00000000-0000-4000-8000-000000000066',
        paper_instance_item_id: '00000000-0000-4000-8000-000000000063',
        order_no: 1, recognition_status: 'illegible', recognized_answer: null, confidence: 0.2,
      }],
    }),
  }))
  await page.route(`**/api/v1/recognition-runs/${runId}/image`, (route) => route.fulfill({
    status: 200, contentType: 'image/png', body: Buffer.from('iVBORw0KGgo=', 'base64'),
  }))
  await page.goto(`/paper-instances/${paperId}/recognize`)
  await page.locator('input[type=file]').setInputFiles({ name: 'answers.jpg', mimeType: 'image/jpeg', buffer: Buffer.from('test') })
  await page.getByRole('button', { name: '上传图片' }).click()
  await page.getByRole('button', { name: '开始提取答案' }).click()
  await expect(page.getByText('无法辨认', { exact: true })).toBeVisible()
  await expect(page.getByText('模型置信度不触发自动确认', { exact: false })).toBeVisible()
  await noOverflow(page)
})
