import { Typography } from 'antd'
import type { AnswerContext } from '../types/assessment'
import { RichQuestionText } from './RichQuestionText'

export function AnswerJudgmentContext({ context }: { context?: AnswerContext | null }) {
  if (!context) return null
  return <details>
    <summary>查看本空题干与位置</summary>
    <Typography.Paragraph type="secondary">
      使用试卷发布时的题干，目标为第 {context.target_subquestion_no} 小题 · 第 {context.target_blank_no} 空，已标为【本空】。
    </Typography.Paragraph>
    <RichQuestionText text={context.question_text} assets={[]} />
  </details>
}
