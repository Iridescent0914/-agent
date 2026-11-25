import { Alert, Button, Card, Space, Statistic, Typography } from 'antd'
import { useEffect, useState } from 'react'
import { apiGet } from '../api/client'
import type { PaperAccuracyComparison as Comparison } from '../types/paperAccuracy'
import type { SubmissionDetail } from '../types/submission'

const statusText: Record<Comparison['data_status'], string> = {
  inactive_paper: '该试卷未发布或已失效，本卷结果不参与有效正确率比较。',
  no_paper_results: '本卷暂无有效已判定作答，暂不能比较正确率。',
  no_history: '排除本卷后暂无有效已确认历史作答，暂不能比较正确率。',
  incomplete_paper: '本卷尚未全部有效判定；当前正确率仅反映已判定题组，暂不宜直接比较。',
  available: '两项正确率可供人工参考；历史样本量、题目难度和知识点差异仍需结合实际判断。',
}

function Percent({ title, value }: { title: string; value: number | null }) {
  return <Statistic title={title} value={value == null ? '—' : value * 100}
    precision={value == null ? undefined : 1} suffix={value == null ? undefined : '%'} />
}

export function PaperAccuracyComparison({ data }: { data: SubmissionDetail }) {
  const [comparison, setComparison] = useState<Comparison | null>(null)
  const [error, setError] = useState(false)
  const [refresh, setRefresh] = useState(0)
  // Every grading/adoption response replaces data; a changed revision invalidates this view.
  const revisionKey = JSON.stringify([data.submission_id, data.is_active,
    data.items.map(item => [item.id, item.attempt?.revision_no, item.attempt?.grade_status, item.attempt?.is_correct])])
  useEffect(() => {
    const controller = new AbortController()
    setComparison(null); setError(false)
    apiGet<Comparison>(`/paper-instances/${data.paper_instance_id}/accuracy-comparison`, controller.signal)
      .then(value => { if (!controller.signal.aborted) setComparison(value) })
      .catch(() => { if (!controller.signal.aborted) setError(true) })
    return () => controller.abort()
  }, [data.paper_instance_id, revisionKey, refresh])

  return <Card title="本卷与历史表现参考" aria-label="本卷与历史表现参考"
    extra={<Button size="small" onClick={() => setRefresh(value => value + 1)}>刷新统计</Button>}>
    {error ? <Typography.Text type="secondary">比较统计暂不可用，可刷新重试；不影响答案录入和批改。</Typography.Text>
      : !comparison ? <Typography.Text type="secondary">正在读取统计…</Typography.Text>
        : <Space direction="vertical" size="middle" className="full-width">
          <Space wrap size="large">
            <Percent title="本卷已判定正确率" value={comparison.paper.accuracy} />
            <Percent title="其他试卷历史正确率" value={comparison.history.accuracy} />
            <Percent title="本卷判定覆盖率" value={comparison.paper.judged_coverage} />
          </Space>
          <Typography.Text>
            本卷共 {comparison.paper.total_question_count} 题组，已判定 {comparison.paper.confirmed_attempt_count} 题组
            （答对 {comparison.paper.correct_count}、答错 {comparison.paper.incorrect_count}）；
            待复核 {comparison.paper.pending_review_count}、未录入 {comparison.paper.unsubmitted_question_count}、
            无效记录 {comparison.paper.excluded_attempt_count}。
          </Typography.Text>
          <Typography.Text>
            排除本卷的历史：已确认 {comparison.history.confirmed_attempt_count} 次
            （答对 {comparison.history.correct_count}、答错 {comparison.history.incorrect_count}），
            涉及 {comparison.history.distinct_question_count} 道不同题；
            待复核 {comparison.history.pending_review_count}、失效或无效记录 {comparison.history.excluded_attempt_count} 条不计入正确率。
          </Typography.Text>
          <Alert type="info" showIcon message={statusText[comparison.data_status]}
            description="仅展示统计，尚未设定最小样本量、比较系数或异常阈值，不据此判断识别错误或调整评分。" />
          <div>
            <Typography.Paragraph>
              按本卷主知识点核对：{comparison.knowledge_coverage.seen_question_count} 题组有已确认历史作答，
              {comparison.knowledge_coverage.unseen_question_count} 题组暂无已确认历史作答，
              {comparison.knowledge_coverage.unmapped_question_count} 题组知识点未映射。
            </Typography.Paragraph>
            {comparison.knowledge_coverage.points.length > 0 && <details>
              <summary>查看知识点历史覆盖</summary>
              <ul style={{ paddingInlineStart: 20, overflowWrap: 'anywhere' }}>
                {comparison.knowledge_coverage.points.map(point => <li key={point.knowledge_point_code ?? 'unmapped'}>
                  {point.knowledge_point_name}：本卷 {point.paper_question_count} 题组，
                  {point.history_status === 'unmapped' ? '无法核对历史覆盖' : `其他试卷已确认作答 ${point.historical_confirmed_attempt_count} 次`}
                </li>)}
              </ul>
            </details>}
          </div>
          <Typography.Text type="secondary">
            两项正确率均按题组计算，多空题按现有整组结果计一次；待复核、未录入和无效记录不算答错。
            历史取其他试卷当前有效的全部记录，非本次提交时冻结的基线；仅核对主知识点，不代表已经掌握或从未学过。
          </Typography.Text>
        </Space>}
  </Card>
}
