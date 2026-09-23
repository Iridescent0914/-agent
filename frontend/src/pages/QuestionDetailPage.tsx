import { ArrowLeftOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Descriptions, Divider, Empty, Skeleton, Space, Tag, Typography } from 'antd'
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { apiGet } from '../api/client'
import { AnswerView } from '../components/AnswerView'
import { RichQuestionText } from '../components/RichQuestionText'
import type { QuestionDetail } from '../types'

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
            <Tag color="geekblue">{question.question_type === 'choice' ? '选择题' : '填空题'}</Tag>
            <Tag color="gold">{'★'.repeat(question.difficulty)}</Tag>
            <Tag color="green">{question.grading_readiness}</Tag>
          </Space>
          <RichQuestionText text={question.question_text} assets={question.assets} />
          {question.options !== null && question.options !== undefined && (
            <pre className="json-block">{JSON.stringify(question.options, null, 2)}</pre>
          )}
          <Divider />
          <Typography.Title level={5}>结构化标准答案</Typography.Title>
          <AnswerView answer={question.correct_answer} />
          <Descriptions bordered size="small" column={{ xs: 1, md: 2 }}>
            <Descriptions.Item label="答案匹配类型">{question.answer_match_type}</Descriptions.Item>
            <Descriptions.Item label="来源审核状态">{question.source_review_status}</Descriptions.Item>
            <Descriptions.Item label="知识点">{question.main_knowledge_point.name}</Descriptions.Item>
            <Descriptions.Item label="主考点">{question.main_exam_point.name}</Descriptions.Item>
            <Descriptions.Item label="章节 / 节">{question.chapter.name} / {question.section}</Descriptions.Item>
            <Descriptions.Item label="原始题号">{question.source_question_number}</Descriptions.Item>
            <Descriptions.Item label="来源文件" span={2}>{question.source_file}</Descriptions.Item>
          </Descriptions>
          <Typography.Title level={5}>参考答案原文</Typography.Title>
          <Typography.Paragraph className="preserve-lines">{question.reference_answer_raw}</Typography.Paragraph>
          <Typography.Title level={5}>易错分析</Typography.Title>
          {question.wrong_analysis ? <Typography.Paragraph className="preserve-lines">{question.wrong_analysis}</Typography.Paragraph> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无易错分析" />}
        </Space>
      </Card>
    </Space>
  )
}
