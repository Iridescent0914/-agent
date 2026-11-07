import { Descriptions, Tag, Typography } from 'antd'
import { answerMatchTypeLabel } from '../utils/gradingLabels'
import type { AssetInfo } from '../types'
import { RichQuestionText } from './RichQuestionText'

function scalar(value: unknown): string {
  if (Array.isArray(value)) return value.map(scalar).join(' / ')
  if (value === null || value === undefined) return '—'
  if (typeof value === 'object') return JSON.stringify(value, null, 2)
  return String(value)
}

export function AnswerView({ answer, assets = [] }: { answer: unknown; assets?: AssetInfo[] }) {
  function content(value: unknown) {
    const text = scalar(value)
    return /\[ASSET:|\\\(|\\\[/.test(text)
      ? <RichQuestionText text={text} assets={assets} />
      : <Typography.Text code>{text}</Typography.Text>
  }
  if (
    typeof answer === 'object' &&
    answer !== null &&
    'kind' in answer &&
    (answer as { kind?: string }).kind === 'group'
  ) {
    const items = (answer as { items?: Array<{
      item_id: string
      subquestion_no?: number
      blank_no?: number
      answer: unknown
      accepted_answers?: string[]
      answer_match_type?: string
    }> }).items || []
    return (
      <Descriptions bordered size="small" column={1}>
        {items.map((item) => (
          <Descriptions.Item key={item.item_id} label={`第 ${item.subquestion_no ?? 1} 小题 · 第 ${item.blank_no ?? 1} 空`}>
            {content(item.answer)}{' '}
            {item.answer_match_type && <Tag>{answerMatchTypeLabel(item.answer_match_type)}</Tag>}
            {item.accepted_answers && item.accepted_answers.length > 1 && <Typography.Text type="secondary"> 可接受：{item.accepted_answers.join(' / ')}</Typography.Text>}
          </Descriptions.Item>
        ))}
      </Descriptions>
    )
  }
  return content(answer)
}
