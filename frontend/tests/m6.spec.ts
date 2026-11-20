import { expect, test, type Page } from '@playwright/test'

const paperId = '00000000-0000-4000-8000-000000000061'

async function noOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy()
}

test('student print view keeps the instance code and exposes no answer key', async ({ page }) => {
  await page.route('**/api/v1/**', route => route.fulfill({ status: 500, json: { message: 'Unexpected offline API' } }))
  await page.route(`**/api/v1/paper-instances/${paperId}/print-view`, (route) => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({
      paper_instance_id: paperId,
      assignment_title: 'M6 有理数练习',
      student_code: 'S001',
      version_no: 1,
      instance_code: 'RAT-M600-0001',
      layout_version: 'answer-card-v2', assignment_type: 'practice',
      is_active: true,
      items: [{
        id: '00000000-0000-4000-8000-000000000063', order_no: 1,
        question_snapshot: { question_text: '计算：-1 + 2 = ____', question_type: 'fill_blank' },
        options_snapshot: null, assets: [],
      }],
      answer_slots: [{ order_no: 1, blank_ids: ['answer'], kind: 'fill_blank', source_module: 'required',
        subquestions: [{ subquestion_no: 1, blanks: [{ item_id: 'answer', blank_no: 1, kind: 'fill_blank' }] }],
      }],
    }),
  }))
  await page.goto(`/paper-instances/${paperId}/print`)
  await expect(page.getByRole('heading', { name: 'M6 有理数练习' })).toBeVisible()
  await expect(page.getByText('RAT-M600-0001', { exact: false }).first()).toBeVisible()
  await expect(page.getByRole('heading', { name: '答题卡', exact: true })).toBeVisible()
  await expect(page.getByText('标准答案', { exact: false })).toHaveCount(0)
  await noOverflow(page)
})

// Old single-image creation UI was retired in S3-E; multi-page capture and
// whole-paper confirmation are covered in answer-pages.spec.ts.
