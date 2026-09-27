import { ArrowLeftOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Descriptions, Divider, Empty, Skeleton, Space, Tag, Typography } from 'antd'
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { apiGet } from '../api/client'
import { AnswerView } from '../components/AnswerView'
import { QuestionReviewEditor } from '../components/QuestionReviewEditor'
import { RichQuestionText } from '../components/RichQuestionText'
import type { QuestionDetail } from '../types'

const typeLabel: Record<string, string> = {
  choice: '选择题',
  fill_blank: '填空题',
  calculation: '计算题',
  solution: '解答题',
  true_false: '判断题',
  unknown: '待确认题型',
}

export function QuestionDetailPage() {
  const { questionId } = useParams()
  const [question, setQuestion] = useState<QuestionDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!questionId) return
    const controller = new AbortController()
    apiGet<QuestionDetail>(`/questions/${questionId}`, controller.signal)
      .then(setQuestion)
      .catch((reason: Error) => {
        if (reason.name !== 'AbortError') setError(reason.message)
      })
    return () => controller.abort()
  }, [questionId])

  if (error) return <Alert type="error" showIcon message="题目加载失败" description={error} />
  if (!question) return <Skeleton active />

  return (
    <Space direction="vertical" size="large" className="page-stack">
      <div><Link to="/questions"><Button icon={<ArrowLeftOutlined />}>返回题库</Button></Link></div>
      <Card>
        <Space direction="vertical" size="middle" className="full-width">
          <Space wrap>
            <Typography.Title level={3} className="no-margin">{question.original_qid}</Typography.Title>
            <Tag color="geekblue">{typeLabel[question.question_type] || question.question_type}</Tag>
            <Tag color="gold">{'★'.repeat(question.difficulty)}</Tag>
            {question.grading_readiness === 'machine_ready'
              ? <Tag color="green">自动组卷池</Tag>
              : <Tag color="orange">待入库验证</Tag>}
          </Space>
          {question.grading_readiness !== 'machine_ready' && (
            <Alert
              type="warning"
              showIcon
              message="这道题不会进入自动组卷"
              description={
                question.question_type === 'choice' || question.question_type === 'fill_blank'
                  ? '老师可以编辑或确认结构化答案；保存审核草稿并通过校验后，可确认进入自动组卷池。'
                  : '当前阶段只开放选择题和填空题审核；计算题、解答题继续保留为待入库题。'
              }
            />
          )}
          {(question.question_type === 'choice' || question.question_type === 'fill_blank') && (
              <div className={'question-review-action' + (question.grading_readiness === 'machine_ready' ? ' is-ready' : '')}>
                <QuestionReviewEditor question={question} onUpdated={setQuestion} />
                <Typography.Text type="secondary">
                  {question.grading_readiness === 'machine_ready'
                    ? '撤回后可修改答案，重新校验通过后再次入池'
                    : '可先保存草稿，校验无误后再确认入池'}
                </Typography.Text>
              </div>
          )}
          <RichQuestionText text={question.question_text} assets={question.assets} />
          {question.options !== null && question.options !== undefined && (
            <pre className="json-block">{JSON.stringify(question.options, null, 2)}</pre>
          )}
          <Divider />
          <Typography.Title level={5}>结构化标准答案</Typography.Title>
          {question.correct_answer === null
            ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未完成结构化答案" />
            : <AnswerView answer={question.correct_answer} />}
          <Descriptions bordered size="small" column={{ xs: 1, md: 2 }}>
            <Descriptions.Item label="答案匹配类型">{question.answer_match_type || '待确认'}</Descriptions.Item>
            <Descriptions.Item label="来源审核状态">{question.source_review_status}</Descriptions.Item>
            <Descriptions.Item label="导入状态">{question.import_status}</Descriptions.Item>
            <Descriptions.Item label="待处理项">{question.import_issues.length || '无'}</Descriptions.Item>
            <Descriptions.Item label="审核版本">{question.review_revision}</Descriptions.Item>
            <Descriptions.Item label="最近审核人">{question.reviewed_by || '尚未人工审核'}</Descriptions.Item>
            <Descriptions.Item label="知识点">{question.main_knowledge_point.name}</Descriptions.Item>
            <Descriptions.Item label="主考点">{question.main_exam_point.name}</Descriptions.Item>
            <Descriptions.Item label="章节 / 节">{question.chapter.name} / {question.section}</Descriptions.Item>
            <Descriptions.Item label="原始题号">{question.source_question_number}</Descriptions.Item>
            <Descriptions.Item label="来源文件" span={2}>{question.source_file}</Descriptions.Item>
          </Descriptions>
          {question.import_issues.length > 0 && (
            <>
              <Typography.Title level={5}>待验证问题</Typography.Title>
              <Space wrap>{question.import_issues.map((issue) => <Tag color="orange" key={issue}>{issue}</Tag>)}</Space>
            </>
          )}
          <Typography.Title level={5}>参考答案原文</Typography.Title>
          <Typography.Paragraph className="preserve-lines">{question.reference_answer_raw}</Typography.Paragraph>
          <Typography.Title level={5}>解析</Typography.Title>
          {question.solution ? <Typography.Paragraph className="preserve-lines">{question.solution}</Typography.Paragraph> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无解析" />}
          <Typography.Title level={5}>易错分析</Typography.Title>
          {question.wrong_analysis ? <Typography.Paragraph className="preserve-lines">{question.wrong_analysis}</Typography.Paragraph> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无易错分析" />}
        </Space>
      </Card>
    </Space>
  )
}
