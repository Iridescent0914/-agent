import type { AssetInfo } from '../types'
import { RichQuestionText } from './RichQuestionText'

interface OptionRow {
  label: string
  content: string
}

function contentText(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (Array.isArray(value)) return value.map(contentText).join('；')
  return Object.entries(value as Record<string, unknown>)
    .map(([key, item]) => `${key}：${contentText(item)}`)
    .join('；')
}

function optionRows(options: unknown): OptionRow[] {
  if (Array.isArray(options)) {
    return options.map((option, index) => {
      const text = contentText(option).trim()
      const parsed = text.match(/^([A-H])\s*[.．、:：]\s*(.*)$/i)
      return parsed
        ? { label: parsed[1].toUpperCase(), content: parsed[2] }
        : { label: String.fromCharCode(65 + index), content: text }
    })
  }
  if (options && typeof options === 'object') {
    return Object.entries(options as Record<string, unknown>).map(([label, content]) => ({
      label: label.replace(/[.．、:：]+$/, '').toUpperCase(),
      content: contentText(content),
    }))
  }
  return []
}

export function QuestionOptions({ options, assets }: { options: unknown; assets: AssetInfo[] }) {
  const rows = optionRows(options)
  if (rows.length === 0) return null
  return (
    <div className='question-options' aria-label='选择题选项'>
      {rows.map((option, index) => (
        <div className='question-option-row' key={`${option.label}-${index}`}>
          <span className='question-option-label'>{option.label}.</span>
          <div className='question-option-content'>
            <RichQuestionText text={option.content} assets={assets} />
          </div>
        </div>
      ))}
    </div>
  )
}
