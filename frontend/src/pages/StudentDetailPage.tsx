import { ArrowLeftOutlined, ReloadOutlined } from '@ant-design/icons'
import { Alert, Button, Select, Skeleton, Space, Statistic, Tabs, Tag, Tooltip, Typography } from 'antd'
import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { apiGet, queryString } from '../api/client'
import { AttemptEvidenceDrawer } from '../components/AttemptEvidenceDrawer'
import { RecommendationPreview } from '../components/RecommendationPreview'
import { StudentAttemptHistory } from '../components/StudentAttemptHistory'
import { StudentProfileOverview } from '../components/StudentProfileOverview'
import type { Page, StudentDetail, StudentSummary } from '../types'
import type { ExamPoint, Profile } from '../types/profile'

export function StudentDetailPage() {
  const { studentId } = useParams()
  return studentId ? <StudentWorkspace key={studentId} studentId={studentId} /> : <Alert type="error" message="学生不存在" />
}

function StudentWorkspace({ studentId }: { studentId: string }) {
  const navigate = useNavigate()
  const [student, setStudent] = useState<StudentDetail | null>(null)
  const [students, setStudents] = useState<StudentSummary[]>([])
  const [profile, setProfile] = useState<Profile | null>(null)
  const [catalog, setCatalog] = useState<ExamPoint[]>([])
  const [error, setError] = useState<string | null>(null)
  const [refresh, setRefresh] = useState(0)
  const [tab, setTab] = useState('profile')
  const [attemptId, setAttemptId] = useState<string | null>(null)
  const [studentQuery, setStudentQuery] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    setStudent(null); setProfile(null); setError(null); setAttemptId(null)
    Promise.all([
      apiGet<StudentDetail>(`/students/${studentId}`, controller.signal),
      apiGet<Profile>(`/students/${studentId}/profile`, controller.signal),
      apiGet<ExamPoint[]>('/exam-points', controller.signal),
    ]).then(([studentData, profileData, examData]) => {
      if (!controller.signal.aborted) { setStudent(studentData); setProfile(profileData); setCatalog(examData) }
    }).catch((reason: Error) => { if (!controller.signal.aborted) setError(reason.message) })
    return () => controller.abort()
  }, [studentId, refresh])
  useEffect(() => {
    if (!student) return
    const controller = new AbortController()
    apiGet<Page<StudentSummary>>(`/students${queryString({
      page: 1,
      page_size: 50,
      class_id: student.class_id,
      keyword: studentQuery,
    })}`, controller.signal).then((result) => {
      if (controller.signal.aborted) return
      setStudents(result.items.some((item) => item.id === student.id)
        ? result.items
        : [student, ...result.items])
    }).catch((reason: Error) => {
      if (!controller.signal.aborted) setError(reason.message)
    })
    return () => controller.abort()
  }, [student, studentQuery])
  return <div className="student-workspace">
    <Space wrap><Link to="/students"><Button icon={<ArrowLeftOutlined />}>学生列表</Button></Link>
      <Tooltip title="刷新画像"><Button icon={<ReloadOutlined />} aria-label="刷新画像" onClick={() => setRefresh((value) => value + 1)} /></Tooltip></Space>
    {error ? <Alert type="error" showIcon message="学生详情加载失败" description={error} /> : !student || !profile ? <Skeleton active /> : <>
      <div className="student-heading"><Space wrap align="center"><Typography.Title level={3}>{student.display_name}</Typography.Title><Tag>{student.student_code}</Tag><Tag color={student.is_demo ? 'purple' : 'blue'}>{student.is_demo ? '演示数据' : '教师添加'}</Tag><Typography.Text type="secondary">{student.class_name}</Typography.Text></Space>
        <Select aria-label="切换学生" value={studentId} style={{ width: 220 }} showSearch filterOption={false} onSearch={setStudentQuery} onChange={(id) => navigate(`/students/${id}`)}
          options={students.map((item) => ({ value: item.id, label: `${item.student_code} ${item.display_name}` }))} /></div>
      <div className="profile-summary">
        <Statistic title="有效作答" value={profile.summary.valid_attempt_count} suffix="次" />
        <Statistic title="不同题目" value={profile.summary.distinct_question_count} suffix="题" />
        <Statistic title="已有证据知识点" value={profile.summary.assessed_exam_point_count} />
        <Statistic title="未评估知识点" value={profile.summary.unassessed_exam_point_count} />
        <Statistic title="待复核" value={profile.summary.pending_review_count} />
        <Statistic title="历史作答总数" value={profile.summary.total_attempt_count} />
      </div>
      <Typography.Paragraph type="secondary">{profile.evidence_note}</Typography.Paragraph>
      {profile.summary.invalid_evidence_count > 0 && <Alert type="warning" showIcon message={`${profile.summary.invalid_evidence_count} 条异常证据未计入画像`}
        description={<Space wrap>{profile.excluded_evidence.filter((item) => item.reason !== 'needs_review').map((item) => <Button key={item.attempt_id} type="link" onClick={() => setAttemptId(item.attempt_id)}>{item.reason}</Button>)}</Space>} />}
      <Tabs activeKey={tab} onChange={setTab} items={[{ key: 'profile', label: '画像' }, { key: 'attempts', label: '历史作答' }, { key: 'recommendations', label: '推荐练习' }]} />
      {tab === 'profile' && <StudentProfileOverview key={refresh} profile={profile} onEvidence={setAttemptId} />}
      {tab === 'attempts' && <StudentAttemptHistory studentId={studentId} catalog={catalog} onEvidence={setAttemptId} />}
      {tab === 'recommendations' && <RecommendationPreview studentId={studentId} catalog={catalog} onEvidence={setAttemptId} />}
    </>}
    <AttemptEvidenceDrawer studentId={studentId} attemptId={attemptId} onClose={() => setAttemptId(null)} />
  </div>
}
