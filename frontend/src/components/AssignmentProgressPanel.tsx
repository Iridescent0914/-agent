import { Card, Empty, Progress, Space, Statistic, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { Link } from 'react-router-dom'
import type { AssignmentAnalytics, AssignmentProgressStudent } from '../types/analytics'

const labels = { unsubmitted: '未提交', needs_review: '待复核', completed: '已完成' }

export function AssignmentProgressPanel({ data }: { data: AssignmentAnalytics | null }) {
  if (!data) return <Empty description="暂无可展示的有效发布作业" />
  const columns: ColumnsType<AssignmentProgressStudent> = [
    { title: '学生', render: (_, row) => `${row.student_code} · ${row.student_name}` },
    { title: '版本', width: 70, render: (_, row) => `v${row.version_no}` },
    { title: '实例码', dataIndex: 'instance_code', width: 150 },
    { title: '状态', width: 110, render: (_, row) => <Tag color={row.grading_status === 'completed' ? 'green' : row.grading_status === 'needs_review' ? 'orange' : 'blue'}>{labels[row.grading_status]}{row.needs_review_count ? ` ${row.needs_review_count}` : ''}</Tag> },
    { title: '操作', width: 100, render: (_, row) => <Link to={`/paper-instances/${row.paper_instance_id}/grading`}>{row.grading_status === 'unsubmitted' ? '录入答案' : '查看批改'}</Link> },
  ]
  const completion = data.summary.completion_rate === null ? 0 : data.summary.completion_rate * 100
  return <Space direction="vertical" size="middle" className="full-width">
    <div className="section-heading">
      <div><Typography.Title level={4}>{data.assignment_title}</Typography.Title><Typography.Text type="secondary">当前有效发布版本；历史作废版本不重复计入</Typography.Text></div>
      <Link to={`/assignments/${data.assignment_id}`}>查看作业详情</Link>
    </div>
    <div className="analytics-progress-grid">
      <Card size="small"><Statistic title="有效发布" value={data.summary.active_published_count} suffix="份" /></Card>
      <Card size="small"><Statistic title="未提交" value={data.summary.unsubmitted_count} suffix="份" /></Card>
      <Card size="small"><Statistic title="待复核" value={data.summary.needs_review_count} suffix="份" /></Card>
      <Card size="small"><Statistic title="已完成" value={data.summary.completed_count} suffix="份" /></Card>
    </div>
    <div><Typography.Text>完成率 {data.summary.completed_count}/{data.summary.active_published_count}</Typography.Text><Progress percent={Number(completion.toFixed(1))} status="active" /></div>
    <Table rowKey="paper_instance_id" columns={columns} dataSource={data.students} pagination={{ pageSize: 50, showSizeChanger: true, pageSizeOptions: [20, 50, 100, 200] }} scroll={{ x: 660 }} />
  </Space>
}
