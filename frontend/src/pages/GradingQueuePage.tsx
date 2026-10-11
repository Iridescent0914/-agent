import { Alert, Button, Card, Select, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { apiGet, queryString } from '../api/client'
import type { GradingQueue, GradingQueueItem, GradingStatus } from '../types/submission'

const statusLabels: Record<GradingStatus, string> = {
  unsubmitted: '未录入', needs_review: '待复核', completed: '已完成',
}

export function GradingQueuePage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const requestedStatus = searchParams.get('status')
  const [data, setData] = useState<GradingQueue>({ items: [], total: 0 })
  const [status, setStatus] = useState<GradingStatus | undefined>(
    requestedStatus === 'unsubmitted' || requestedStatus === 'needs_review' || requestedStatus === 'completed' ? requestedStatus : undefined,
  )
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)
  const load = () => {
    setLoading(true); setError(null)
    apiGet<GradingQueue>(`/grading/queue${queryString({
      status, offset: (page - 1) * pageSize, limit: pageSize,
    })}`)
      .then(setData).catch((reason) => setError((reason as Error).message)).finally(() => setLoading(false))
  }
  useEffect(load, [status, page, pageSize])
  const columns: ColumnsType<GradingQueueItem> = [
    { title: '作业', dataIndex: 'assignment_title' },
    { title: '学生', render: (_, row) => `${row.student_code} · ${row.student_name}` },
    { title: '版本', width: 80, render: (_, row) => `v${row.version_no}` },
    { title: '实例码', dataIndex: 'instance_code', width: 150 },
    { title: '题量', dataIndex: 'item_count', width: 70 },
    { title: '批改状态', width: 110, render: (_, row) => <Tag color={row.grading_status === 'completed' ? 'green' : row.grading_status === 'needs_review' ? 'orange' : 'blue'}>{statusLabels[row.grading_status]}{row.needs_review_count ? ` ${row.needs_review_count}` : ''}</Tag> },
    { title: '试卷状态', width: 100, render: (_, row) => <Tag color={row.is_active ? 'green' : 'default'}>{row.is_active ? '有效' : '历史版本'}</Tag> },
    { title: '操作', width: 100, render: (_, row) => <Link to={`/paper-instances/${row.paper_instance_id}/grading`}>{row.grading_status === 'unsubmitted' ? '录入答案' : '查看批改'}</Link> },
  ]
  return <Space direction="vertical" size="large" className="page-stack grading-workspace">
    {error && <Alert type="error" showIcon message="加载失败" description={error} action={<Button onClick={load}>重试</Button>} />}
    <div className="student-heading"><div><Typography.Title level={2}>作业批改</Typography.Title><Typography.Text type="secondary">手动录入、确定性判分与人工复核</Typography.Text></div><Select allowClear placeholder="全部状态" value={status} onChange={(value) => { setStatus(value); setPage(1); setSearchParams(value ? { status: value } : {}, { replace: true }) }} options={Object.entries(statusLabels).map(([value, label]) => ({ value, label }))} style={{ width: 150 }} /></div>
    <Card className="table-card"><Table rowKey="paper_instance_id" loading={loading} columns={columns} dataSource={data.items} pagination={{
      current: page, pageSize, total: data.total, showSizeChanger: true,
      pageSizeOptions: [20, 50, 100, 200], showTotal: (total) => `共 ${total} 份试卷`,
    }} onChange={(pagination) => {
      setPage(pagination.current ?? 1); setPageSize(pagination.pageSize ?? 50)
    }} scroll={{ x: 900 }} /></Card>
  </Space>
}
