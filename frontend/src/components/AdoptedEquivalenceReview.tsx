import { Card, Space, Tag, Typography } from 'antd'
import type { SubmissionPaperItem } from '../types/submission'
import { equivalenceAdoption } from '../utils/equivalenceAdoption'

export function AdoptedEquivalenceReview({ item }: { item: SubmissionPaperItem }) {
  const review = equivalenceAdoption(item)
  if (!review) return null
  return <Card size="small" title={`第 ${item.order_no} 题 · 答案建议采用记录`}>
    <Space direction="vertical" className="full-width" size="small">
      <Space wrap>
        <Tag color={review.current ? 'blue' : undefined}>{review.current ? '已采用建议' : '历史采用记录'}</Tag>
        <Typography.Text>采用 {review.leaves.length} 空 · 评分修订 v{review.revision}</Typography.Text>
      </Space>
      {!review.current && <Typography.Text type="secondary">
        本题已有后续修订；以下为历史采用记录，当前评分以最新修订为准。
      </Typography.Text>}
      {review.leaves.map(leaf => <Space key={leaf.itemId} wrap>
        <Typography.Text>{leaf.label}</Typography.Text>
        <Tag color={review.current ? leaf.acceptable ? 'green' : 'red' : undefined}>{leaf.verdict}</Tag>
      </Space>)}
      {review.reviewer && <Typography.Text type="secondary">核对人：{review.reviewer}</Typography.Text>}
      {review.reason && <Typography.Text type="secondary">采用原因：{review.reason}</Typography.Text>}
    </Space>
  </Card>
}
