import { Alert, Button, Drawer, Skeleton, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { apiGet, queryString } from '../api/client'
import type { ExamPointStudentAnalytics, ExamPointStudentList } from '../types/analytics'

const statusLabel = { assessed: '证据较稳定', insufficient: '证据较少', unassessed: '未评估' }

export function ExamPointStudentDrawer({
  examPointCode,
  className,
  chapterCode,
  onClose,
}: {
  examPointCode: string | null
  className: string
  chapterCode: string
  onClose: () => void
}) {
  const [data, setData] = useState<ExamPointStudentList | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    if (!examPointCode) return
    const controller = new AbortController()
    setData(null); setError(null)
    apiGet<ExamPointStudentList>(
      `/analytics/classes/exam-points/${encodeURIComponent(examPointCode)}/students${queryString({ class_name: className, chapter_code: chapterCode })}`,
      controller.signal,
    ).then(setData).catch((reason: Error) => {
      if (!controller.signal.aborted) setError(reason.message)
    })
    return () => controller.abort()
  }, [examPointCode, className, chapterCode, retry])

  const columns: ColumnsType<ExamPointStudentAnalytics> = [
    { title: '学生', render: (_, row) => <Link to={`/students/${row.student_id}`}>{row.student_code} · {row.student_name}</Link> },
    { title: '证据状态', width: 110, render: (_, row) => <Tag color={row.assessment_status === 'assessed' ? 'green' : row.assessment_status === 'insufficient' ? 'default' : 'blue'}>{statusLabel[row.assessment_status]}</Tag> },
    { title: '掌握度参考值', width: 130, render: (_, row) => row.mastery === null ? '未评估' : row.mastery.toFixed(4) },
    { title: '正确率', width: 120, render: (_, row) => row.accuracy === null ? '—' : `${(row.accuracy * 100).toFixed(1)}%（${row.correct_count}/${row.attempt_count}）` },
    { title: '最近错误', dataIndex: 'recent_wrong_count', width: 90 },
  ]
  return <Drawer
    open={Boolean(examPointCode)}
    onClose={onClose}
    width={760}
    title={data ? `${data.exam_point_name} · 学生证据` : '考点学生证据'}
    className="analytics-drawer"
  >
    <Typography.Paragraph type="secondary">未评估不是 0 分；正确率只统计当前有效的确认作答。</Typography.Paragraph>
    {error ? <Alert type="error" showIcon message="学生证据加载失败" description={error} action={<Button onClick={() => setRetry((value) => value + 1)}>重试</Button>} />
      : !data ? <Skeleton active />
        : <Table rowKey="student_id" columns={columns} dataSource={data.items} pagination={false} scroll={{ x: 680 }} />}
  </Drawer>
}
