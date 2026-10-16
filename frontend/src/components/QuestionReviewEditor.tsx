import { DeleteOutlined, FormOutlined, PlusOutlined } from '@ant-design/icons'
import {
  Alert,
  Button,
  Drawer,
  Form,
  Input,
  Popconfirm,
  Radio,
  Select,
  Space,
  Typography,
  message,
} from 'antd'
import { useState } from 'react'
import { apiPost, apiPut } from '../api/client'
import type {
  QuestionAnswerItemInput,
  QuestionAnswerMatchType,
  QuestionDetail,
  QuestionReviewRequest,
  QuestionWithdrawRequest,
} from '../types'
import { MathAnswerInput } from './MathAnswerInput'

interface StoredItem {
  item_id?: unknown
  answer?: unknown
  answer_match_type?: unknown
}

function initialItems(question: QuestionDetail): QuestionAnswerItemInput[] {
  const answer = question.correct_answer
  const rawItems =
    typeof answer === 'object' &&
    answer !== null &&
    'kind' in answer &&
    (answer as { kind?: string }).kind === 'group'
      ? ((answer as { items?: StoredItem[] }).items || [])
      : [{ item_id: '1', answer, answer_match_type: question.answer_match_type }]

  const used = new Set<string>()
  return (rawItems.length ? rawItems : [{ item_id: '1' }]).map((item, index) => {
    let itemId = String(item.item_id || index + 1)
    const base = itemId
    let suffix = 2
    while (used.has(itemId)) itemId = `${base}_${suffix++}`
    used.add(itemId)
    const sourceType = String(item.answer_match_type || '')
    const matchType: QuestionAnswerMatchType =
      question.question_type === 'choice'
        ? 'choice'
        : sourceType === 'numeric' || sourceType === 'latex_exact'
          ? sourceType
          : 'exact'
    return {
      item_id: itemId,
      answer: item.answer === null || item.answer === undefined ? '' : String(item.answer),
      answer_match_type: matchType,
    }
  })
}

interface Props {
  question: QuestionDetail
  onUpdated: (question: QuestionDetail) => void
}

export function QuestionReviewEditor({ question, onUpdated }: Props) {
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<QuestionAnswerItemInput[]>([])
  const [reviewerName, setReviewerName] = useState('任课教师')
  const [reviewNote, setReviewNote] = useState('')
  const [revision, setRevision] = useState(question.review_revision)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [withdrawn, setWithdrawn] = useState(false)

  function show() {
    setItems(initialItems(question))
    setRevision(question.review_revision)
    setReviewNote('')
    setError(null)
    setWithdrawn(false)
    setOpen(true)
  }

  function updateItem(index: number, patch: Partial<QuestionAnswerItemInput>) {
    setItems((current) =>
      current.map((item, itemIndex) => (itemIndex === index ? { ...item, ...patch } : item)),
    )
  }

  function addItem() {
    const existing = new Set(items.map((item) => item.item_id))
    let number = items.length + 1
    while (existing.has(String(number))) number += 1
    setItems((current) => [
      ...current,
      {
        item_id: String(number),
        answer: '',
        answer_match_type: question.question_type === 'choice' ? 'choice' : 'exact',
      },
    ])
  }

  async function submit(promote: boolean) {
    setError(null)
    const normalized = items.map((item) => ({
      ...item,
      item_id: item.item_id.trim(),
      answer: item.answer.trim(),
    }))
    if (!reviewerName.trim() || normalized.some((item) => !item.item_id || !item.answer)) {
      setError('请填写审核人、答案项编号和每一项答案。')
      return
    }
    if (new Set(normalized.map((item) => item.item_id)).size !== normalized.length) {
      setError('答案项编号不能重复。')
      return
    }

    const body: QuestionReviewRequest = {
      expected_review_revision: revision,
      reviewer_name: reviewerName.trim(),
      review_note: reviewNote.trim() || undefined,
      items: normalized,
    }
    setSubmitting(true)
    try {
      const updated = promote
        ? await apiPost<QuestionDetail>(`/questions/${question.id}/promote`, body)
        : await apiPut<QuestionDetail>(`/questions/${question.id}/review-draft`, body)
      onUpdated(updated)
      setRevision(updated.review_revision)
      setItems(initialItems(updated))
      message.success(promote ? '已确认并进入自动组卷池' : '审核草稿已保存')
      if (promote) setOpen(false)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '提交失败')
    } finally {
      setSubmitting(false)
    }
  }

  const isChoice = question.question_type === 'choice'
  const isTrueFalse = question.question_type === 'true_false'
  const isReady = question.grading_readiness === 'machine_ready' && !withdrawn

  async function withdraw() {
    setError(null)
    if (!reviewerName.trim()) {
      setError('请填写操作人。')
      return
    }
    const body: QuestionWithdrawRequest = {
      expected_review_revision: revision,
      reviewer_name: reviewerName.trim(),
      review_note: reviewNote.trim() || undefined,
    }
    setSubmitting(true)
    try {
      const updated = await apiPost<QuestionDetail>(`/questions/${question.id}/withdraw`, body)
      onUpdated(updated)
      setRevision(updated.review_revision)
      setItems(initialItems(updated))
      setReviewNote('')
      setWithdrawn(true)
      message.success('已撤回到待审核状态，现在可以修改答案')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '撤回失败')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <>
      <Button
        type={question.grading_readiness === 'machine_ready' ? 'default' : 'primary'}
        danger={question.grading_readiness === 'machine_ready'}
        size="large"
        icon={<FormOutlined />}
        className="question-review-cta"
        onClick={show}
      >
        {question.grading_readiness === 'machine_ready'
          ? '撤回并重新编辑'
          : '审核答案并进入组卷池'}
      </Button>
      <Drawer
        title={`${isReady ? '撤回题目' : '审核答案'} · ${question.original_qid}`}
        width="min(760px, 100vw)"
        open={open}
        onClose={() => setOpen(false)}
        destroyOnClose
        extra={<Typography.Text type="secondary">版本 {revision}</Typography.Text>}
      >
        <Space direction="vertical" size="large" className="full-width">
          <Alert
            type={isReady ? 'warning' : 'info'}
            showIcon
            message={
              isReady
                ? '这道题当前在自动组卷池中'
                : isChoice
                  ? '选择题使用选项匹配'
                  : isTrueFalse
                    ? '判断题逐小题选择正确或错误'
                    : '填空题可使用数值、文本或公式匹配'
            }
            description={
              isReady
                ? '撤回后，新生成的作业不会再选到本题；既有试卷快照不受影响。撤回完成后可立即修改答案。'
                : isChoice
                  ? '每个小题选择一个 A-H 选项。'
                  : isTrueFalse
                    ? '每个小题选择“正确”或“错误”。'
                    : '普通数值优先选择“数值”；含分数、根式等公式时选择“公式（LaTeX 精确匹配）”。第一版暂不判断代数等价。'
            }
          />
          <Form layout="vertical">
            <Form.Item label="审核人" required>
              <Input value={reviewerName} maxLength={80} onChange={(event) => setReviewerName(event.target.value)} />
            </Form.Item>
            <Form.Item label="审核说明">
              <Input.TextArea
                value={reviewNote}
                maxLength={500}
                showCount
                rows={2}
                placeholder="例如：已对照原卷逐项核验"
                onChange={(event) => setReviewNote(event.target.value)}
              />
            </Form.Item>
          </Form>

          {isReady ? (
            <>
              {error && <Alert type="error" showIcon message="无法撤回" description={error} />}
              <Popconfirm
                title="确认撤回到待审核状态？"
                description="撤回后本题将不再参与新的自动组卷，完成修改并重新确认后才能再次入池。"
                okText="确认撤回"
                cancelText="取消"
                onConfirm={() => void withdraw()}
              >
                <Button danger type="primary" size="large" loading={submitting}>
                  确认撤回到待审核
                </Button>
              </Popconfirm>
            </>
          ) : (
            <>
          {items.map((item, index) => (
            <div className="answer-item-editor" key={index}>
              <div className="answer-item-heading">
                <Typography.Text strong>答案项 {index + 1}</Typography.Text>
                <Button
                  danger
                  type="text"
                  icon={<DeleteOutlined />}
                  disabled={items.length === 1}
                  onClick={() => setItems((current) => current.filter((_, itemIndex) => itemIndex !== index))}
                >
                  删除
                </Button>
              </div>
              <div className="answer-item-grid">
                <Form.Item label="小题/空格编号" required>
                  <Input
                    value={item.item_id}
                    maxLength={64}
                    onChange={(event) => updateItem(index, { item_id: event.target.value })}
                  />
                </Form.Item>
                <Form.Item label="判分方式" required>
                  <Select
                    value={item.answer_match_type}
                    disabled={isChoice || isTrueFalse}
                    options={
                      isChoice
                        ? [{ value: 'choice', label: '选项匹配' }]
                        : isTrueFalse
                          ? [{ value: 'exact', label: '判断匹配' }]
                        : [
                            { value: 'numeric', label: '数值（整数/小数）' },
                            { value: 'exact', label: '精确文本' },
                            { value: 'latex_exact', label: '公式（LaTeX 精确匹配）' },
                          ]
                    }
                    onChange={(value: QuestionAnswerMatchType) =>
                      updateItem(index, { answer_match_type: value })
                    }
                  />
                </Form.Item>
              </div>
              <Form.Item label="标准答案" required>
                {isChoice ? (
                  <Select
                    value={item.answer || undefined}
                    placeholder="选择正确选项"
                    options={'ABCDEFGH'.split('').map((value) => ({ value, label: value }))}
                    onChange={(answer) => updateItem(index, { answer })}
                  />
                ) : isTrueFalse ? (
                  <Radio.Group
                    value={item.answer || undefined}
                    onChange={(event) => updateItem(index, { answer: event.target.value })}
                  >
                    <Radio.Button value="正确">正确</Radio.Button>
                    <Radio.Button value="错误">错误</Radio.Button>
                  </Radio.Group>
                ) : item.answer_match_type === 'latex_exact' ? (
                  <MathAnswerInput
                    value={item.answer}
                    onChange={(answer) => updateItem(index, { answer })}
                  />
                ) : (
                  <Input
                    value={item.answer}
                    placeholder={item.answer_match_type === 'numeric' ? '例如：-2.5' : '输入标准答案'}
                    onChange={(event) => updateItem(index, { answer: event.target.value })}
                  />
                )}
              </Form.Item>
            </div>
          ))}

          <Button icon={<PlusOutlined />} onClick={addItem}>添加答案项</Button>
          {error && <Alert type="error" showIcon message="无法提交" description={error} />}
          <Space wrap>
            <Button loading={submitting} onClick={() => void submit(false)}>保存审核草稿</Button>
            <Popconfirm
              title="确认进入自动组卷池？"
              description="系统会再次校验答案；确认后，本题可以被后续自动组卷选中。"
              okText="确认入池"
              cancelText="取消"
              onConfirm={() => void submit(true)}
            >
              <Button type="primary" loading={submitting}>校验并确认入池</Button>
            </Popconfirm>
          </Space>
            </>
          )}
        </Space>
      </Drawer>
    </>
  )
}
