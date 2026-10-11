import { Descriptions, Tag, Typography } from 'antd'
import { answerMatchTypeLabel } from '../utils/gradingLabels'

function scalar(value: unknown): string {
  if (Array.isArray(value)) return value.map(scalar).join(' / ')
  if (value === null || value === undefined) return '—'
  if (typeof value === 'object') return JSON.stringify(value, null, 2)
  return String(value)
}

export function AnswerView({ answer }: { answer: unknown }) {
  if (
    typeof answer === 'object' &&
    answer !== null &&
    'kind' in answer &&
    (answer as { kind?: string }).kind === 'group'
  ) {
    const items = (answer as { items?: Array<{ item_id: string; answer: unknown; answer_match_type?: string }> }).items || []
    return (
      <Descriptions bordered size="small" column={1}>
        {items.map((item) => (
          <Descriptions.Item key={item.item_id} label={item.item_id}>
            <Typography.Text code>{scalar(item.answer)}</Typography.Text>{' '}
            {item.answer_match_type && <Tag>{answerMatchTypeLabel(item.answer_match_type)}</Tag>}
          </Descriptions.Item>
        ))}
      </Descriptions>
    )
  }
  return <Typography.Text code>{scalar(answer)}</Typography.Text>
}
