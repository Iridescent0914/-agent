import { DeleteOutlined, SearchOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Col, Empty, Form, Input, Modal, Row, Select, Space, Statistic, Table, Tag, Typography, message } from 'antd'
import type { ColumnsType, TablePaginationConfig } from 'antd/es/table'
import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { apiDelete, apiGet } from '../api/client'
import type { Page, QuestionCatalogChapter, QuestionCatalogStats, QuestionSummary } from '../types'

const typeLabel: Record<string, string> = {
  choice: '选择题',
  fill_blank: '填空题',
  calculation: '计算题',
  solution: '解答题',
  true_false: '判断题',
  unknown: '待确认题型',
}

function cleanPreview(value: string) {
  return value.replace(/\[ASSET:[^\]]+\]/g, '[公式]').replace(/\s+/g, ' ').slice(0, 100)
}

export function QuestionListPage() {
  const [filterForm] = Form.useForm()
  const [searchParams, setSearchParams] = useSearchParams()
  const [data, setData] = useState<Page<QuestionSummary> | null>(null)
  const [stats, setStats] = useState<QuestionCatalogStats | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [catalog, setCatalog] = useState<QuestionCatalogChapter[]>([])
  const [refreshKey, setRefreshKey] = useState(0)
  const page = Number(searchParams.get('page') || 1)
  const pageSize = Number(searchParams.get('page_size') || 20)
  const filterChapterCode = Form.useWatch('chapter_code', filterForm)
  const filterKnowledgePoints = catalog.find((chapter) => chapter.code === filterChapterCode)?.knowledge_points || []
  const filterSections = catalog.find((chapter) => chapter.code === filterChapterCode)?.sections || []

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    apiGet<Page<QuestionSummary>>(`/questions?${searchParams.toString()}`, controller.signal)
      .then(setData)
      .catch((reason: Error) => {
        if (reason.name !== 'AbortError') setError(reason.message)
      })
      .finally(() => setLoading(false))
    return () => controller.abort()
  }, [searchParams, refreshKey])

  useEffect(() => {
    const controller = new AbortController()
    apiGet<QuestionCatalogStats>('/questions/stats', controller.signal)
      .then(setStats)
      .catch(() => undefined)
    return () => controller.abort()
  }, [refreshKey])

  useEffect(() => {
    apiGet<QuestionCatalogChapter[]>('/questions/catalog')
      .then(setCatalog)
      .catch((reason: Error) => setError(reason.message))
  }, [])

  function deleteQuestion(record: QuestionSummary) {
    Modal.confirm({
      title: `删除题目 ${record.original_qid}？`,
      content: '题目将退出题库和后续组卷；既有试卷快照与历史作答会保留。',
      okText: '确认删除', cancelText: '取消', okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await apiDelete(`/questions/${record.id}`, { operator_name: '任课教师' })
          message.success('题目已删除')
          setRefreshKey((value) => value + 1)
        } catch (reason) {
          setError((reason as Error).message)
        }
      },
    })
  }

  const columns: ColumnsType<QuestionSummary> = [
    {
      title: '题目',
      dataIndex: 'original_qid',
      width: 360,
      render: (_, record) => (
        <Space direction="vertical" size={2}>
          <Link to={`/questions/${record.id}`}><Typography.Text strong>{record.original_qid}</Typography.Text></Link>
          <Typography.Text type="secondary">{cleanPreview(record.question_text)}</Typography.Text>
        </Space>
      ),
    },
    { title: '题型', dataIndex: 'question_type', width: 90, render: (value) => typeLabel[value] || value },
    {
      title: '题库状态',
      dataIndex: 'grading_readiness',
      width: 145,
      render: (value, record) => value === 'machine_ready'
        ? <Tag color="green">自动组卷池</Tag>
        : <Space direction="vertical" size={2}>
            <Tag color="orange">待入库验证</Tag>
            {record.import_issue_count > 0 && <Typography.Text type="secondary">{record.import_issue_count} 项待处理</Typography.Text>}
          </Space>,
    },
    { title: '难度', dataIndex: 'difficulty', width: 100, render: (value: number) => '★'.repeat(value) },
    { title: '年级', dataIndex: 'grade', width: 110 },
    { title: '主属节', dataIndex: 'section', width: 180 },
    { title: '节内主属 KP', dataIndex: ['main_knowledge_point', 'name'], width: 210 },
    { title: '核心素养', dataIndex: 'core_literacy', width: 150 },
    { title: '必做', dataIndex: 'is_required', width: 70, render: (value: boolean) => value ? <Tag color="red">是</Tag> : '否' },
    {
      title: '操作',
      key: 'action',
      width: 210,
      fixed: 'right',
      render: (_, record) => {
        const canEdit = record.question_type === 'choice' || record.question_type === 'fill_blank' || record.question_type === 'true_false'
        const isPending = record.grading_readiness === 'raw_answer_only'
        const label = canEdit ? (isPending ? '去审核' : '撤回/编辑') : '查看'
        return <Space>
          <Link to={`/questions/${record.id}`}>
            <Button
              type={canEdit && isPending ? 'primary' : canEdit ? 'default' : 'link'}
              danger={canEdit && !isPending}
            >
              {label}
            </Button>
          </Link>
          <Button danger type='text' icon={<DeleteOutlined />} onClick={() => deleteQuestion(record)}>删除</Button>
        </Space>
      },
    },
  ]

  function update(values: Record<string, string | number | undefined>) {
    const next = new URLSearchParams(searchParams)
    Object.entries(values).forEach(([key, value]) => {
      if (value === undefined || value === '') next.delete(key)
      else next.set(key, String(value))
    })
    setSearchParams(next)
  }

  const pagination: TablePaginationConfig = {
    current: page,
    pageSize,
    total: data?.total || 0,
    showSizeChanger: true,
    showTotal: (total) => `共 ${total} 组题`,
    onChange: (nextPage, nextSize) => update({ page: nextPage, page_size: nextSize }),
  }

  return (
    <Space direction="vertical" size="large" className="page-stack">
      <div className='student-heading'>
        <div>
          <Typography.Title level={2}>题库</Typography.Title>
          <Typography.Text type='secondary'>可按章、主属节和节内主属 KP 逐级筛选；只有结构化答案可靠的题目才进入自动组卷池。</Typography.Text>
        </div>
      </div>
      <Row gutter={16}>
        <Col xs={24} md={8}><Card><Statistic title="原始题库" value={stats?.total ?? 0} suffix="组" /></Card></Col>
        <Col xs={12} md={8}><Card><Statistic title="自动组卷池" value={stats?.ready_count ?? 0} suffix="组" valueStyle={{ color: '#237804' }} /></Card></Col>
        <Col xs={12} md={8}><Card><Statistic title="待入库验证" value={stats?.pending_count ?? 0} suffix="组" valueStyle={{ color: '#d46b08' }} /></Card></Col>
      </Row>
      <Alert
        type="info"
        showIcon
        message="自动组卷池与待入库题已隔离"
        description="待入库题可查看题干、原始参考答案和技术问题，但不会被推荐算法选入学生作业。"
      />
      <Card>
        <Form
          form={filterForm}
          layout="vertical"
          initialValues={{
            keyword: searchParams.get('keyword') || '',
            chapter_code: searchParams.get('chapter_code') || undefined,
            section: searchParams.get('section') || undefined,
            knowledge_point_code: searchParams.get('knowledge_point_code') || undefined,
            question_type: searchParams.get('question_type') || undefined,
            grading_readiness: searchParams.get('grading_readiness') || undefined,
            difficulty: searchParams.get('difficulty') ? Number(searchParams.get('difficulty')) : undefined,
          }}
          onFinish={(values) => update({ ...values, page: 1 })}
        >
          <Row gutter={16} align="bottom">
            <Col xs={24} md={6}><Form.Item name="keyword" label="关键词或 qid"><Input allowClear prefix={<SearchOutlined />} /></Form.Item></Col>
            <Col xs={12} md={4}><Form.Item name="chapter_code" label="章节"><Select allowClear options={catalog.map((chapter) => ({ value: chapter.code, label: chapter.name }))} onChange={() => filterForm.setFieldsValue({ section: undefined, knowledge_point_code: undefined })} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name="section" label="主属节"><Select allowClear disabled={!filterChapterCode} options={filterSections.map((value) => ({ value, label: value }))} /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name="knowledge_point_code" label="节内主属 KP"><Select allowClear showSearch optionFilterProp="label" disabled={!filterChapterCode} options={filterKnowledgePoints.map((point) => ({ value: point.code, label: point.name }))} /></Form.Item></Col>
          </Row>
          <Row gutter={16} align="bottom">
            <Col xs={12} md={6}><Form.Item name="grading_readiness" label="题库状态"><Select allowClear options={[{ value: 'machine_ready', label: '自动组卷池' }, { value: 'raw_answer_only', label: '待入库验证' }]} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name="question_type" label="题型"><Select allowClear options={Object.entries(typeLabel).map(([value, label]) => ({ value, label }))} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name="difficulty" label="难度"><Select allowClear options={[1, 2, 3, 4, 5].map((value) => ({ value, label: `${value} 星` }))} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item><Button type="primary" htmlType="submit" block>筛选</Button></Form.Item></Col>
          </Row>
        </Form>
      </Card>
      {error && <Alert type="error" showIcon message="题库加载失败" description={error} />}
      <Card className="table-card">
        <Table
          rowKey="id"
          columns={columns}
          dataSource={data?.items || []}
          loading={loading}
          pagination={pagination}
          locale={{ emptyText: <Empty description="没有符合条件的题目" /> }}
          scroll={{ x: 1650 }}
        />
      </Card>
    </Space>
  )
}
