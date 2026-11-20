import { readFileSync } from 'node:fs'
import { expect, test } from '@playwright/test'

const paperId = '00000000-0000-4000-8000-000000000071'
const references = JSON.parse(readFileSync(new URL('../../data/interim/rational_all.json', import.meta.url), 'utf8')) as Array<{
  original_qid: string
  question_type: 'choice' | 'fill_blank'
  correct_answer: { items: Array<{ item_id: string; subquestion_no: number; blank_no: number }> }
}>

test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/internal/stage6/status', route => route.fulfill({ json: { enabled: false } }))
})

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

test('one print action combines questions and frozen answer pages', async ({ page }, testInfo) => {
  const data = fixture()
  const prefix = `/api/v1/internal/stage6/paper-instances/${paperId}/answer-card-artifacts`
  const artifact = {
    id: '00000000-0000-4000-8000-000000000072', paper_instance_id: paperId,
    artifact_code: 'A123456789ABCDEF', revision_no: 1, total_pages: 3,
    layout_version: 'answer-card-v3', renderer_version: 'fixture',
    source_sha256: 'a'.repeat(64), pdf_sha256: 'b'.repeat(64), manifest_sha256: 'c'.repeat(64),
    created_at: '2026-11-18T00:00:00Z', reused: false,
    pdf_url: `${prefix}/00000000-0000-4000-8000-000000000072/pdf`, manifest_url: `${prefix}/00000000-0000-4000-8000-000000000072/manifest`,
  }
  let posts = 0
  await page.addInitScript(() => { window.print = () => { document.documentElement.dataset.printCalls = String(Number(document.documentElement.dataset.printCalls || 0) + 1) } })
  await page.route('**/api/v1/paper-instances/*/print-view', route => route.fulfill({ json: data }))
  await page.route('**/api/v1/internal/stage6/status', route => route.fulfill({ json: { enabled: true } }))
  await page.route(`**${prefix}`, route => {
    if (route.request().method() === 'POST') {
      expect(route.request().postDataJSON()).toEqual({ expected_version_no: 1 })
      posts++
      return route.fulfill({ json: artifact })
    }
    return route.fulfill({ json: [] })
  })
  await page.route(`**${artifact.manifest_url}`, route => route.fulfill({ json: {
    artifact_id: artifact.id, paper_instance_id: paperId, version_no: 1, total_pages: 3,
    pages: [{ page_no: 1 }, { page_no: 2 }, { page_no: 3 }],
  } }))
  await page.route(`**${prefix}/${artifact.id}/pages/*/image`, route => {
    const number = route.request().url().match(/pages\/(\d+)\/image/)![1]
    return route.fulfill({ contentType: 'image/svg+xml', body: `<svg xmlns="http://www.w3.org/2000/svg" width="595.276" height="841.89" viewBox="0 0 595.276 841.89"><rect x="24" y="24" width="547" height="763" fill="white" stroke="black"/><text x="40" y="70" font-size="20">ANSWER_AREA_PAGE_${number}</text><text x="40" y="810" font-size="10">RAT-CARD-0001 ${artifact.artifact_code} ${number}/3</text></svg>` })
  })
  await page.goto(`/paper-instances/${paperId}/print`)
  const print = page.getByRole('button', { name: '打印 / 保存 PDF' })
  await expect(page.getByRole('button', { name: '生成识别用答题卡' })).toHaveCount(0)
  await expect(page.getByRole('link', { name: '下载识别用答题卡' })).toHaveCount(0)
  expect(posts).toBe(0)
  await print.click()
  await expect(page.locator('html')).toHaveAttribute('data-print-calls', '1')
  await expect(page.locator('.frozen-answer-page')).toHaveCount(3)
  await expect(page.locator('.print-question')).toHaveCount(3)
  expect(posts).toBe(1)
  await print.click()
  await expect(page.locator('html')).toHaveAttribute('data-print-calls', '2')
  expect(posts).toBe(1)
  await page.emulateMedia({ media: 'print' })
  await expect(print).toBeHidden()
  expect(await page.locator('.frozen-answer-page').evaluateAll(elements => elements.every(el => getComputedStyle(el).page === 'frozen-answer' && getComputedStyle(el).breakBefore === 'page'))).toBeTruthy()
  if (testInfo.project.name === 'desktop') {
    await page.pdf({ path: testInfo.outputPath('combined-student-paper.pdf'), preferCSSPageSize: true, printBackground: true })
  }
})

test('failed print preparation blocks printing and keeps the existing paper visible', async ({ page }) => {
  const data = fixture()
  await page.addInitScript(() => { window.print = () => { document.documentElement.dataset.printCalled = 'yes' } })
  await page.route('**/api/v1/paper-instances/*/print-view', route => route.fulfill({ json: data }))
  await page.route('**/api/v1/internal/stage6/status', route => route.fulfill({ json: { enabled: true } }))
  await page.route('**/answer-card-artifacts', route => route.fulfill({ status: 409, json: { message: '试卷版本已变更，请刷新后生成' } }))
  await page.goto(`/paper-instances/${paperId}/print`)
  await page.getByRole('button', { name: '打印 / 保存 PDF' }).click()
  await expect(page.getByText('试卷版本已变更，请刷新后生成')).toBeVisible()
  await expect(page.locator('html')).not.toHaveAttribute('data-print-called', 'yes')
  await expect(page.locator('.print-question')).toHaveCount(3)
  await expect(page.locator('.answer-blank')).toHaveCount(39)
})
