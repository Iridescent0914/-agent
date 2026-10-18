import { ArrowLeftOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Descriptions, Divider, Empty, Skeleton, Space, Tag, Typography } from 'antd'
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { apiGet } from '../api/client'
import { AnswerView } from '../components/AnswerView'
import { QuestionReviewEditor } from '../components/QuestionReviewEditor'
import { RichQuestionText } from '../components/RichQuestionText'
import { QuestionOptions } from '../components/QuestionOptions'
import { QuestionContentEditor } from '../components/QuestionContentEditor'
import type { QuestionDetail } from '../types'
import { answerMatchTypeLabel } from '../utils/gradingLabels'

const typeLabel: Record<string, string> = {
  choice: '选择题',
  fill_blank: '填空题',
  calculation: '计算题',
  solution: '解答题',
  true_false: '判断题',
  unknown: '待确认题型',
}

const importIssueLabel: Record<string, string> = {
  missing_question_text: '题干缺失',
  missing_reference_answer: '参考答案缺失',
  missing_section: '主属节缺失',
  missing_main_knowledge_point: '节内主属 KP 缺失',
  missing_difficulty: '难度缺失',
  multiple_subquestions: '包含多个小题，需要核对答案项',
  question_type_mismatch: '原始题型与题干结构不一致，需要核对',
  options_not_structured: '选择题选项尚未结构化',
  answer_items_not_structured: '答案尚未拆分为可判分结构',
  source_contains_ai_draft_marker: '原始资料包含待确认的 AI 草拟内容',
  asset_unavailable: '部分题目图片或公式资源无法访问',
}

function describeImportIssue(issue: string) {
  return importIssueLabel[issue] || `其他导入问题：${issue}`
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
            {question.is_required && <Tag color="red">必做题</Tag>}
            {question.grading_readiness === 'machine_ready'
              ? <Tag color="green">自动组卷池</Tag>
              : <Tag color="orange">待入库验证</Tag>}
            <QuestionContentEditor question={question} onUpdated={setQuestion} />
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
          {(question.question_type === 'choice' || question.question_type === 'fill_blank' || question.question_type === 'true_false') && (
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
            <QuestionOptions options={question.options} assets={question.assets} />
          )}
          <Divider />
          <Typography.Title level={5}>结构化标准答案</Typography.Title>
          {question.correct_answer === null
            ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未完成结构化答案" />
            : <AnswerView answer={question.correct_answer} />}
          <Descriptions bordered size="small" column={{ xs: 1, md: 2 }}>
            <Descriptions.Item label="答案匹配类型">{answerMatchTypeLabel(question.answer_match_type)}</Descriptions.Item>
            <Descriptions.Item label="来源审核状态">{question.source_review_status}</Descriptions.Item>
            <Descriptions.Item label="导入状态">{question.import_status}</Descriptions.Item>
            <Descriptions.Item label="当前待处理项">{question.import_issues.length || '无'}</Descriptions.Item>
            <Descriptions.Item label="审核版本">{question.review_revision}</Descriptions.Item>
            <Descriptions.Item label="最近审核人">{question.reviewed_by || '尚未人工审核'}</Descriptions.Item>
            <Descriptions.Item label="年级">{question.grade}</Descriptions.Item>
            <Descriptions.Item label="章">{question.chapter.name}</Descriptions.Item>
            <Descriptions.Item label="主属节">{question.section}</Descriptions.Item>
            <Descriptions.Item label="节内主属 KP">{question.main_knowledge_point.name}</Descriptions.Item>
            <Descriptions.Item label="核心素养">{question.core_literacy}</Descriptions.Item>
            <Descriptions.Item label="是否为必做题">{question.is_required ? '是' : '否'}</Descriptions.Item>
            <Descriptions.Item label="原始题号">{question.source_question_number}</Descriptions.Item>
            <Descriptions.Item label="来源文件" span={2}>{question.source_file}</Descriptions.Item>
          </Descriptions>
          {question.import_issues.length > 0 && (
            <>
              <Typography.Title level={5}>当前需要处理</Typography.Title>
              <Space wrap>{question.import_issues.map((issue) => <Tag color="orange" key={issue}>{describeImportIssue(issue)}</Tag>)}</Space>
            </>
          )}
          <Typography.Title level={5}>参考答案原文</Typography.Title>
          <RichQuestionText text={question.reference_answer_raw} assets={question.assets} />
          <Typography.Title level={5}>解析</Typography.Title>
          {question.solution ? <RichQuestionText text={question.solution} assets={question.assets} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无解析" />}
          <Typography.Title level={5}>易错分析</Typography.Title>
          {question.wrong_analysis ? <RichQuestionText text={question.wrong_analysis} assets={question.assets} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无易错分析" />}
        </Space>
      </Card>
    </Space>
  )
}
