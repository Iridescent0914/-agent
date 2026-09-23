import { ArrowLeftOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Descriptions, Empty, Segmented, Skeleton, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { apiGet, queryString } from '../api/client'
import { AnswerView } from '../components/AnswerView'
import type { Page, StudentAttempt, StudentDetail } from '../types'

type CorrectFilter = 'all' | 'correct' | 'wrong'

export function StudentDetailPage() {
  const { studentId } = useParams()
  const [student, setStudent] = useState<StudentDetail | null>(null)
  const [attempts, setAttempts] = useState<Page<StudentAttempt> | null>(null)
  const [filter, setFilter] = useState<CorrectFilter>('all')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!studentId) return
    const controller = new AbortController()
    Promise.all([
      apiGet<StudentDetail>(`/students/${studentId}`, controller.signal),
      apiGet<Page<StudentAttempt>>(`/students/${studentId}/attempts${queryString({ page: 1, page_size: 100, is_correct: filter === 'all' ? undefined : filter === 'correct' })}`, controller.signal),
    ])
      .then(([studentData, attemptData]) => { setStudent(studentData); setAttempts(attemptData) })
      .catch((reason: Error) => { if (reason.name !== 'AbortError') setError(reason.message) })
    return () => controller.abort()
  }, [studentId, filter])

  if (error) return <Alert type="error" showIcon message="学生详情加载失败" description={error} />
  if (!student || !attempts) return <Skeleton active />

  const columns: ColumnsType<StudentAttempt> = [
    { title: '时间', dataIndex: 'answered_at', width: 170, render: (value) => new Date(value).toLocaleString('zh-CN') },
    { title: '题目', dataIndex: 'original_qid', width: 230, render: (_, row) => <Link to={`/questions/${row.question_id}`}>{row.original_qid}</Link> },
    { title: '考点', dataIndex: 'main_exam_point_name', width: 170 },
    { title: '难度', dataIndex: 'difficulty', width: 90, render: (value: number) => '★'.repeat(value) },
    { title: '学生答案', dataIndex: 'student_answer', width: 220, render: (value) => <AnswerView answer={value} /> },
    { title: '结论', dataIndex: 'is_correct', width: 90, render: (value: boolean | null) => value === null ? <Tag>待复核</Tag> : value ? <Tag color="success">正确</Tag> : <Tag color="error">错误</Tag> },
  ]

  return (
    <Space direction="vertical" size="large" className="page-stack">
      <div><Link to="/students"><Button icon={<ArrowLeftOutlined />}>返回学生列表</Button></Link></div>
      <Card>
        <Space direction="vertical" size="middle" className="full-width">
          <Space wrap><Typography.Title level={3} className="no-margin">{student.display_name}</Typography.Title><Tag color="purple">{student.student_code}</Tag><Tag>演示数据</Tag></Space>
          <Descriptions bordered size="small" column={{ xs: 1, md: 3 }}>
            <Descriptions.Item label="班级">{student.class_name}</Descriptions.Item>
            <Descriptions.Item label="历史作答">{student.attempt_count}</Descriptions.Item>
            <Descriptions.Item label="历史正确">{student.correct_count}</Descriptions.Item>
          </Descriptions>
          <Alert type="info" showIcon message={student.evidence_note} />
        </Space>
      </Card>
      <Card title="历史作答证据" extra={<Segmented value={filter} onChange={(value) => setFilter(value as CorrectFilter)} options={[{ label: '全部', value: 'all' }, { label: '正确', value: 'correct' }, { label: '错误', value: 'wrong' }]} />}>
        <Table rowKey="id" columns={columns} dataSource={attempts.items} pagination={{ pageSize: 20, showSizeChanger: false }} scroll={{ x: 1050 }} locale={{ emptyText: <Empty description="没有符合条件的历史作答" /> }} />
      </Card>
    </Space>
  )
}
