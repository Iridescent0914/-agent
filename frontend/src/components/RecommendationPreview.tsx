import { PlayCircleOutlined } from '@ant-design/icons'
import { Alert, Button, Checkbox, Collapse, Empty, Form, InputNumber, Select, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiPost } from '../api/client'
import type { KnowledgePoint, Recommendation, RecommendationParams, RecommendedQuestion, ScoreParts } from '../types/profile'

const factorNames: Record<keyof ScoreParts, string> = { weakness: '练习优先程度', recent_wrong: '近期错误', difficulty_match: '难度匹配', forgetting: '回顾间隔' }
const reasonNames: Record<string, string> = {
  chapter: '章节题库', grading_readiness: '可自动判分', no_questions_in_scope: '教学范围内无题',
  question_type_filter: '题型限制', difficulty_filter: '难度限制', recently_practiced: '近期已练',
  knowledge_point_cap: '知识点题量上限', insufficient_scope_inventory: '范围内题量不足',
}
const defaults: RecommendationParams = {
  chapter_code: '', new_knowledge_point_codes: [], review_knowledge_point_codes: [],
  new_count: 0, review_count: 6, question_types: ['choice', 'fill_blank'],
  base_difficulty: 2, min_difficulty: 1, max_difficulty: 5, avoid_recent_days: 7, max_knowledge_point_ratio: 0.5,
}

export function RecommendationPreview({ studentId, catalog, onEvidence }: {
  studentId: string; catalog: KnowledgePoint[]; onEvidence: (id: string) => void
}) {
  const [form] = Form.useForm<RecommendationParams>()
  const chapterCode = Form.useWatch('chapter_code', form)
  const newScope = Form.useWatch('new_knowledge_point_codes', form) ?? []
  const reviewScope = Form.useWatch('review_knowledge_point_codes', form) ?? []
  const [result, setResult] = useState<Recommendation | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const request = useRef<AbortController | null>(null)
  useEffect(() => () => request.current?.abort(), [])
  useEffect(() => {
    if (!chapterCode && catalog[0]) form.setFieldValue('chapter_code', catalog[0].chapter_code)
  }, [catalog, chapterCode, form])
  const invalidate = () => { request.current?.abort(); setLoading(false); setResult(null); setError(null) }
  const submit = async (values: RecommendationParams) => {
    request.current?.abort()
    const controller = new AbortController()
    request.current = controller
    setLoading(true); setResult(null); setError(null)
    try {
      const data = await apiPost<Recommendation>(`/students/${studentId}/recommendations/preview`, { ...defaults, ...values }, controller.signal)
      if (!controller.signal.aborted) setResult(data)
    } catch (reason) {
      if (!controller.signal.aborted) setError((reason as Error).message)
    } finally { if (!controller.signal.aborted) setLoading(false) }
  }
  const chapterCatalog = catalog.filter((point) => point.chapter_code === chapterCode)
  const chapterOptions = Array.from(new Set(catalog.map((point) => point.chapter_code)))
    .map((code) => ({ value: code, label: code }))
  const scopeOptions = (disabled: string[]) => chapterCatalog.map((point) => ({ value: point.code,
    label: `${point.name}（${point.available_question_count} 题）`, disabled: disabled.includes(point.code) }))
  const columns: ColumnsType<RecommendedQuestion> = [
    { title: '题目', dataIndex: 'original_qid', width: 220, render: (_, row) => <Link to={`/questions/${row.question_id}`}>{row.original_qid}</Link> },
    { title: '类别', width: 94, render: (_, row) => <Tag color={row.bucket === 'new' ? 'cyan' : 'gold'}>{row.bucket === 'new' ? '新知识' : '回顾'}</Tag> },
    { title: '主要知识点', dataIndex: 'knowledge_point_name', width: 200 },
    { title: '难度', dataIndex: 'difficulty', width: 70 },
    { title: '推荐理由', dataIndex: 'reason_text', width: 350 },
    { title: '优先分', dataIndex: 'score', width: 90, render: (value: number) => value.toFixed(4) },
  ]
  return <section className="profile-section">
    <Form form={form} layout="vertical" initialValues={defaults} onFinish={submit} onValuesChange={invalidate}>
      <Form.Item label="章节" name="chapter_code" rules={[{ required: true }]}><Select options={chapterOptions} onChange={() => form.setFieldsValue({ new_knowledge_point_codes: [], review_knowledge_point_codes: [] })} /></Form.Item>
      <div className="recommendation-scope-grid">
        <Form.Item label="新知识范围" name="new_knowledge_point_codes"><Select aria-label="新知识范围" mode="multiple" options={scopeOptions(reviewScope)} showSearch optionFilterProp="label" placeholder="选择知识点" maxTagCount="responsive" /></Form.Item>
        <Form.Item label="回顾范围" name="review_knowledge_point_codes"><Select aria-label="回顾范围" mode="multiple" options={scopeOptions(newScope)} showSearch optionFilterProp="label" placeholder="选择已学知识点" maxTagCount="responsive" /></Form.Item>
      </div>
      <div className="recommendation-controls">
        <Form.Item label="新知识题量" name="new_count" rules={[{ required: true }]}><InputNumber min={0} max={30} precision={0} /></Form.Item>
        <Form.Item label="回顾题量" name="review_count" rules={[{ required: true }]}><InputNumber min={0} max={30} precision={0} /></Form.Item>
        <Form.Item label="基础难度" name="base_difficulty" rules={[{ required: true }]}><InputNumber min={1} max={5} precision={0} /></Form.Item>
        <Form.Item label="最低难度" name="min_difficulty" rules={[{ required: true }]}><InputNumber min={1} max={5} precision={0} /></Form.Item>
        <Form.Item label="最高难度" name="max_difficulty" rules={[{ required: true }]}><InputNumber min={1} max={5} precision={0} /></Form.Item>
        <Form.Item label="避免重复天数" name="avoid_recent_days" tooltip="0 表示关闭近期重复过滤" rules={[{ required: true }]}><InputNumber min={0} max={3650} precision={0} /></Form.Item>
        <Form.Item label="单知识点最大占比" name="max_knowledge_point_ratio" rules={[{ required: true }]}><InputNumber min={0.05} max={1} step={0.05} /></Form.Item>
      </div>
      <div className="recommendation-actions">
        <Form.Item label="题型" name="question_types" rules={[{ type: 'array', min: 1, required: true, message: '至少选择一种题型' }]}><Checkbox.Group options={[{ label: '选择题', value: 'choice' }, { label: '填空题', value: 'fill_blank' }, { label: '判断题', value: 'true_false' }]} /></Form.Item>
        <Button type="primary" htmlType="submit" icon={<PlayCircleOutlined />} loading={loading}>预览推荐</Button>
      </div>
    </Form>
    {error && <Alert type="error" showIcon message="推荐预览失败" description={error} />}
    {!result && !error && !loading && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚无推荐结果" />}
    {result && <div className="recommendation-result">
      <Alert showIcon type={result.shortfall ? 'warning' : 'success'} message={`请求 ${result.requested_count} 题，推荐 ${result.selected_count} 题，缺口 ${result.shortfall} 题`}
        description={result.shortfall ? `新知识缺 ${result.new_shortfall} 题，回顾缺 ${result.review_shortfall} 题。` : undefined} />
      <Typography.Paragraph type="secondary" className="result-meta">基准时间：{new Date(result.as_of).toLocaleString('zh-CN')} · 单知识点最多 {result.max_per_knowledge_point} 题</Typography.Paragraph>
      <Table rowKey="question_id" columns={columns} dataSource={result.items} pagination={false} scroll={{ x: 1060 }}
        locale={{ emptyText: <Empty description="当前条件下没有可推荐题目" /> }}
        expandable={{ expandedRowRender: (item) => <div className="score-details">
          <Tag>{item.strategy === 'diagnostic' ? '基础诊断' : '依据历史推荐'}</Tag>
          <Table size="small" pagination={false} rowKey="key" dataSource={(Object.keys(factorNames) as Array<keyof ScoreParts>).map((key) => ({ key, name: factorNames[key], value: item.components[key], weight: result.weights[key], contribution: item.contributions[key] }))}
            columns={[{ title: '因素', dataIndex: 'name' }, { title: '分值', dataIndex: 'value', render: (value: number) => value.toFixed(4) }, { title: '权重', dataIndex: 'weight' }, { title: '加权贡献', dataIndex: 'contribution', render: (value: number) => value.toFixed(4) }]} />
          {item.evidence.length > 0 && <Select className="evidence-select" aria-label="推荐依据作答" placeholder="查看依据作答" value={null} onChange={onEvidence}
            options={item.evidence.map((ref, index) => ({ value: ref.attempt_id, label: `作答 ${index + 1} · 修订 ${ref.revision_no}` }))} />}
        </div> }} />
      <Collapse ghost items={result.diagnostics.filter((entry) => entry.requested_count > 0).map((entry) => ({ key: entry.bucket,
        label: `${entry.bucket === 'new' ? '新知识' : '回顾'}候选筛选：${entry.selected_count} / ${entry.requested_count} 题`,
        children: <Space direction="vertical" className="full-width">
          <Space wrap>{entry.reason_codes.map((code) => <Tag key={code} color="warning">{reasonNames[code] ?? code}</Tag>)}</Space>
          <Table size="small" pagination={false} rowKey="code" dataSource={entry.stages} columns={[
            { title: '筛选环节', dataIndex: 'code', render: (code: string) => code === 'no_questions_in_scope' ? '教师指定范围' : reasonNames[code] ?? code },
            { title: '剩余', dataIndex: 'remaining' }, { title: '本步排除', dataIndex: 'removed' },
          ]} />
        </Space>,
      }))} />
    </div>}
  </section>
}
