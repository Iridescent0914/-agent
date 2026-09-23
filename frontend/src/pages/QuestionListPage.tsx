import { SearchOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Col, Empty, Form, Input, Row, Select, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType, TablePaginationConfig } from 'antd/es/table'
import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { apiGet, queryString } from '../api/client'
import type { Page, QuestionSummary } from '../types'

const typeLabel: Record<string, string> = { choice: '选择题', fill_blank: '填空题' }

function cleanPreview(value: string) {
  return value.replace(/\[ASSET:[^\]]+\]/g, '[公式]').replace(/\s+/g, ' ').slice(0, 100)
}

export function QuestionListPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [data, setData] = useState<Page<QuestionSummary> | null>(null)
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
    { title: '难度', dataIndex: 'difficulty', width: 100, render: (value: number) => '★'.repeat(value) },
    { title: '知识点', dataIndex: ['main_knowledge_point', 'name'], width: 170 },
    { title: '主考点', dataIndex: ['main_exam_point', 'name'], width: 180 },
    {
      title: '资源',
      width: 110,
      render: (_, record) => record.asset_count > 0 ? <Tag color="blue">公式 {record.asset_count}</Tag> : <Tag>纯文本</Tag>,
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
        <Typography.Text type="secondary">当前使用 M0 已审核且可确定性判分的 30 组种子题。</Typography.Text>
      </div>
      <Card>
        <Form
          layout="vertical"
          initialValues={{
            keyword: searchParams.get('keyword') || '',
            question_type: searchParams.get('question_type') || undefined,
            difficulty: searchParams.get('difficulty') ? Number(searchParams.get('difficulty')) : undefined,
          }}
          onFinish={(values) => update({ ...values, page: 1 })}
        >
          <Row gutter={16} align="bottom">
            <Col xs={24} md={12} lg={10}><Form.Item name="keyword" label="关键词或 qid"><Input allowClear prefix={<SearchOutlined />} /></Form.Item></Col>
            <Col xs={12} md={5}><Form.Item name="question_type" label="题型"><Select allowClear options={[{ value: 'choice', label: '选择题' }, { value: 'fill_blank', label: '填空题' }]} /></Form.Item></Col>
            <Col xs={12} md={4}><Form.Item name="difficulty" label="难度"><Select allowClear options={[1, 2, 3, 4, 5].map((value) => ({ value, label: `${value} 星` }))} /></Form.Item></Col>
            <Col xs={24} md={3}><Form.Item><Button type="primary" htmlType="submit" block>筛选</Button></Form.Item></Col>
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
          scroll={{ x: 1050 }}
        />
      </Card>
    </Space>
  )
}
