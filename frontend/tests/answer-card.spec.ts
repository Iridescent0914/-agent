import { readFileSync } from 'node:fs'
import { expect, test } from '@playwright/test'

const paperId = '00000000-0000-4000-8000-000000000071'
const references = JSON.parse(readFileSync(new URL('../../data/interim/rational_all.json', import.meta.url), 'utf8')) as Array<{
  original_qid: string
  question_type: 'choice' | 'fill_blank'
  correct_answer: { items: Array<{ item_id: string; subquestion_no: number; blank_no: number }> }
}>

function fixture() {
  const chosen = ['001', '020', '050'].map(suffix => references.find(q => q.original_qid.endsWith(`.${suffix}`))!)
  return {
    paper_instance_id: paperId, assignment_title: '多空题答题卡检查', student_code: 'S001',
    instance_code: 'RAT-CARD-0001', version_no: 1, layout_version: 'answer-card-v2',
    assignment_type: 'practice', is_active: true,
    items: chosen.map((q, index) => ({
      id: `item-${index}`, order_no: index + 1, question_snapshot: {
        question_text: `题组 ${index + 1}：按小题顺序作答。`, question_type: q.question_type,
      }, options_snapshot: null, assets: [],
    })),
    answer_slots: chosen.map((q, index) => {
      const numbers = [...new Set(q.correct_answer.items.map(part => part.subquestion_no))]
      return {
        order_no: index + 1, kind: q.question_type,
        source_module: ['required', 'history', 'personalized'][index],
        blank_ids: q.correct_answer.items.map(part => part.item_id),
        subquestions: numbers.map(number => ({
          subquestion_no: number,
          blanks: q.correct_answer.items.filter(part => part.subquestion_no === number).map(part => ({
            item_id: part.item_id, blank_no: part.blank_no, kind: q.question_type,
          })),
        })),
      }
    }),
  }
}

test('reference answer units fit in three modules on screen and in print', async ({ page }, testInfo) => {
  const data = fixture()
  await page.route('**/api/v1/paper-instances/*/print-view', route => route.fulfill({ json: data }))
  await page.goto(`/paper-instances/${paperId}/print`)
  const card = page.getByRole('region', { name: '答题卡', exact: true })
  await expect(card.locator('.answer-module-heading')).toHaveText(['一、必做题', '二、个性化', '三、历史回顾'])
  await expect(card.locator('.answer-card-question h4')).toHaveText([
    /第 1 题/, /第 3 题/, /第 2 题/,
  ])
  await expect(card.locator('.answer-blank')).toHaveCount(39)
  expect(await card.innerText()).not.toMatch(/sub_\d|blank_\d|标准答案/)
  const coordinates = await card.locator('.answer-blank').evaluateAll(elements => elements.map(el => el.getAttribute('aria-label')))
  expect(new Set(coordinates).size).toBe(39)
  await expect(card.locator('.answer-subquestion-number').last()).toHaveText('第 2 题（14）')

  async function checkBoundaries() {
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy()
    expect(await card.locator('.answer-blank').evaluateAll(elements => elements.every(el => {
      const field = el.getBoundingClientRect()
      const table = el.closest('table')!.getBoundingClientRect()
      return field.left >= table.left - 1 && field.right <= table.right + 1 && field.height >= 50
    }))).toBeTruthy()
  }
  await checkBoundaries()
  await card.screenshot({ path: testInfo.outputPath('answer-card-screen.png') })
  await page.emulateMedia({ media: 'print' })
  await page.setViewportSize({ width: 688, height: 1016 })
  await checkBoundaries()
  await expect(page.locator('.print-toolbar')).toBeHidden()
  expect(await card.evaluate(el => getComputedStyle(el).breakBefore)).toBe('page')
  expect(await card.locator('thead').evaluate(el => getComputedStyle(el).display)).toBe('table-header-group')
  expect(await page.locator('.app-layout').evaluate(el => getComputedStyle(el).backgroundColor)).toBe('rgb(255, 255, 255)')
  if (testInfo.project.name === 'desktop') {
    await page.pdf({ path: testInfo.outputPath('answer-card.pdf'), preferCSSPageSize: true, printBackground: true })
  }
})

test('empty ordinary modules stay visible and weekly summary keeps its own section', async ({ page }) => {
  const data = fixture()
  data.answer_slots = data.answer_slots.slice(0, 1)
  await page.route('**/api/v1/paper-instances/*/print-view', route => route.fulfill({ json: data }))
  await page.goto(`/paper-instances/${paperId}/print`)
  await expect(page.getByText('本次无个性化。')).toBeVisible()
  await expect(page.getByText('本次无历史回顾。')).toBeVisible()

  data.assignment_type = 'weekly_summary'
  data.answer_slots[0].source_module = 'weekly_summary'
  await page.reload()
  const card = page.getByRole('region', { name: '答题卡', exact: true })
  await expect(card.locator('.answer-module-heading')).toHaveText(['一、周总结'])
  await expect(card.locator('.answer-blank')).toHaveCount(13)
})
