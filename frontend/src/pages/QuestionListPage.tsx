import { SearchOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Col, Empty, Form, Input, Row, Select, Space, Statistic, Table, Tag, Typography } from 'antd'
import type { ColumnsType, TablePaginationConfig } from 'antd/es/table'
import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { apiGet } from '../api/client'
import type { Page, QuestionCatalogStats, QuestionSummary } from '../types'

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
  const [searchParams, setSearchParams] = useSearchParams()
  const [data, setData] = useState<Page<QuestionSummary> | null>(null)
  const [stats, setStats] = useState<QuestionCatalogStats | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const page = Number(searchParams.get('page') || 1)
  const pageSize = Number(searchParams.get('page_size') || 20)

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
  }, [searchParams])

  useEffect(() => {
    const controller = new AbortController()
    apiGet<QuestionCatalogStats>('/questions/stats', controller.signal)
      .then(setStats)
      .catch(() => undefined)
    return () => controller.abort()
  }, [])

  const columns: ColumnsType<QuestionSummary> = useMemo(() => [
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
    { title: '知识点', dataIndex: ['main_knowledge_point', 'name'], width: 170 },
    { title: '主考点', dataIndex: ['main_exam_point', 'name'], width: 180 },
    {
      title: '资源',
      width: 110,
      render: (_, record) => record.asset_count > 0 ? <Tag color="blue">公式 {record.asset_count}</Tag> : <Tag>纯文本</Tag>,
    },
    {
      title: '操作',
      key: 'action',
      width: 110,
      fixed: 'right',
      render: (_, record) => {
        const canEdit = record.question_type === 'choice' || record.question_type === 'fill_blank'
        const isPending = record.grading_readiness === 'raw_answer_only'
        const label = canEdit ? (isPending ? '去审核' : '撤回/编辑') : '查看'
        return (
          <Link to={`/questions/${record.id}`}>
            <Button
              type={canEdit && isPending ? 'primary' : canEdit ? 'default' : 'link'}
              danger={canEdit && !isPending}
            >
              {label}
            </Button>
          </Link>
        )
      },
    },
  ], [])

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
      <div>
        <Typography.Title level={2}>有理数题库</Typography.Title>
        <Typography.Text type="secondary">完整题库用于教师查看；只有结构化答案可靠的题目才进入自动组卷池。</Typography.Text>
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
          layout="vertical"
          initialValues={{
            keyword: searchParams.get('keyword') || '',
            question_type: searchParams.get('question_type') || undefined,
            grading_readiness: searchParams.get('grading_readiness') || undefined,
            difficulty: searchParams.get('difficulty') ? Number(searchParams.get('difficulty')) : undefined,
          }}
          onFinish={(values) => update({ ...values, page: 1 })}
        >
          <Row gutter={16} align="bottom">
            <Col xs={24} md={8}><Form.Item name="keyword" label="关键词或 qid"><Input allowClear prefix={<SearchOutlined />} /></Form.Item></Col>
            <Col xs={12} md={4}><Form.Item name="grading_readiness" label="题库状态"><Select allowClear options={[{ value: 'machine_ready', label: '自动组卷池' }, { value: 'raw_answer_only', label: '待入库验证' }]} /></Form.Item></Col>
            <Col xs={12} md={4}><Form.Item name="question_type" label="题型"><Select allowClear options={Object.entries(typeLabel).map(([value, label]) => ({ value, label }))} /></Form.Item></Col>
            <Col xs={12} md={4}><Form.Item name="difficulty" label="难度"><Select allowClear options={[1, 2, 3, 4, 5].map((value) => ({ value, label: `${value} 星` }))} /></Form.Item></Col>
            <Col xs={24} md={4}><Form.Item><Button type="primary" htmlType="submit" block>筛选</Button></Form.Item></Col>
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
          scroll={{ x: 1200 }}
        />
      </Card>
    </Space>
  )
}
