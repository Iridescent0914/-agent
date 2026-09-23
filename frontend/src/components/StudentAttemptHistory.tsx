import { Alert, Button, Empty, Segmented, Select, Space, Table, Tag } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useEffect, useState } from 'react'
import { apiGet, queryString } from '../api/client'
import type { Page, StudentAttempt } from '../types'
import type { ExamPoint } from '../types/profile'
import { AnswerView } from './AnswerView'

export function StudentAttemptHistory({ studentId, catalog, onEvidence }: { studentId: string; catalog: ExamPoint[]; onEvidence: (id: string) => void }) {
  const [filter, setFilter] = useState('all')
  const [exam, setExam] = useState<string | undefined>()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(10)
  const [attempts, setAttempts] = useState<Page<StudentAttempt> | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError(null); setAttempts(null)
    apiGet<Page<StudentAttempt>>(`/students/${studentId}/attempts${queryString({ page, page_size: pageSize, exam_point_code: exam, is_correct: filter === 'all' ? undefined : filter === 'correct' })}`, controller.signal)
      .then((data) => { if (!controller.signal.aborted) setAttempts(data) })
      .catch((reason: Error) => { if (!controller.signal.aborted) setError(reason.message) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [studentId, filter, exam, page, pageSize])
  const columns: ColumnsType<StudentAttempt> = [
    { title: '时间', dataIndex: 'answered_at', width: 170, render: (value: string) => new Date(value).toLocaleString('zh-CN') },
    { title: '题目', dataIndex: 'original_qid', width: 230, render: (_, item) => <Button type="link" className="table-link" onClick={() => onEvidence(item.id)}>{item.original_qid || '快照缺失'}</Button> },
    { title: '当时主考点', dataIndex: 'main_exam_point_name', width: 210 },
    { title: '难度', dataIndex: 'difficulty', width: 70, render: (value: number | null) => value ?? '-' },
    { title: '学生答案', dataIndex: 'student_answer', width: 240, render: (value: unknown) => <AnswerView answer={value} /> },
    { title: '结论', dataIndex: 'is_correct', width: 100, render: (value: boolean | null) => <Tag color={value === null ? 'default' : value ? 'success' : 'error'}>{value === null ? '待复核' : value ? '正确' : '错误'}</Tag> },
    { title: '画像证据', width: 110, render: (_, item) => <span>{item.used_in_profile ? `修订 ${item.revision_no}` : '未计入'}</span> },
  ]
  return <section className="profile-section">
    <Space wrap className="section-toolbar">
      <Segmented value={filter} options={[{ label: '全部', value: 'all' }, { label: '正确', value: 'correct' }, { label: '错误', value: 'wrong' }]} onChange={(value) => { setFilter(value); setPage(1) }} />
      <Select aria-label="历史考点筛选" className="exam-select" value={exam} onChange={(value) => { setExam(value); setPage(1) }} allowClear showSearch optionFilterProp="label" placeholder="全部考点"
        options={catalog.map((item) => ({ value: item.code, label: item.name }))} />
    </Space>
    {error && <Alert message={error} type="error" showIcon />}
    <Table rowKey="id" columns={columns} dataSource={attempts?.items ?? []} loading={loading} scroll={{ x: 1140 }}
      pagination={{ current: page, pageSize, total: attempts?.total ?? 0, showSizeChanger: true, pageSizeOptions: [10, 20, 50], showTotal: (total) => `共 ${total} 条`, onChange: (nextPage, size) => { setPage(size !== pageSize ? 1 : nextPage); setPageSize(size) } }}
      locale={{ emptyText: <Empty description="没有符合条件的历史作答" /> }} />
  </section>
}
