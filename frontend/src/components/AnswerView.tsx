import { Descriptions, Tag, Typography } from 'antd'
import { answerMatchTypeLabel } from '../utils/gradingLabels'
import type { AssetInfo } from '../types'
import { RichQuestionText } from './RichQuestionText'
import { ReadOnlyMath } from './ReadOnlyMath'

function scalar(value: unknown): string {
  if (Array.isArray(value)) return value.map(scalar).join(' / ')
  if (value === null || value === undefined) return '—'
  if (typeof value === 'object') return JSON.stringify(value, null, 2)
  return String(value)
}

export function AnswerView({ answer, assets = [] }: { answer: unknown; assets?: AssetInfo[] }) {
  function content(value: unknown) {
    const text = scalar(value)
    if (/\\(?:overline|frac|sqrt|cdot|times|pm)\b/.test(text) && !/\[ASSET:|\\\(|\\\[/.test(text)) {
      return <ReadOnlyMath latex={text} />
    }
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
            {item.accepted_answers && item.accepted_answers.filter((value) => value !== scalar(item.answer)).length > 0 && (
              <Typography.Text type="secondary"> 可接受：{item.accepted_answers.filter((value) => value !== scalar(item.answer)).map((value, index) => (
                <span key={`${value}-${index}`}>{index > 0 && ' / '}{content(value)}</span>
              ))}</Typography.Text>
            )}
          </Descriptions.Item>
        ))}
      </Descriptions>
    )
  }
  return content(answer)
}
