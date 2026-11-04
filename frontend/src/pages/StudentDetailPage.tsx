import { ArrowLeftOutlined, ReloadOutlined } from '@ant-design/icons'
import { Alert, Button, Select, Skeleton, Space, Tabs, Tag, Tooltip, Typography } from 'antd'
import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { apiGet, queryString } from '../api/client'
import { AttemptEvidenceDrawer } from '../components/AttemptEvidenceDrawer'
import { StudentAttemptHistory } from '../components/StudentAttemptHistory'
import { StudentProfileOverview } from '../components/StudentProfileOverview'
import type { Page, QuestionCatalogChapter, StudentDetail, StudentSummary } from '../types'
import type { KnowledgePoint, Profile } from '../types/profile'

export function StudentDetailPage() {
  const { studentId } = useParams()
  return studentId ? <StudentWorkspace key={studentId} studentId={studentId} /> : <Alert type="error" message="学生不存在" />
}

function StudentWorkspace({ studentId }: { studentId: string }) {
  const navigate = useNavigate()
  const [student, setStudent] = useState<StudentDetail | null>(null)
  const [students, setStudents] = useState<StudentSummary[]>([])
  const [profile, setProfile] = useState<Profile | null>(null)
  const [catalog, setCatalog] = useState<KnowledgePoint[]>([])
  const [questionCatalog, setQuestionCatalog] = useState<QuestionCatalogChapter[]>([])
  const [error, setError] = useState<string | null>(null)
  const [refresh, setRefresh] = useState(0)
  const [tab, setTab] = useState('profile')
  const [attemptId, setAttemptId] = useState<string | null>(null)
  const [studentQuery, setStudentQuery] = useState('')
  const gradeOptions = useMemo(
    () => Array.from(new Set(questionCatalog.flatMap((item) => item.grades ?? []))),
    [questionCatalog],
  )
  const [grade, setGrade] = useState('')
  const visibleChapters = useMemo(
    () => questionCatalog.filter((item) => !grade || (item.grades ?? []).includes(grade)),
    [grade, questionCatalog],
  )
  const [chapterCode, setChapterCode] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    setStudent(null); setProfile(null); setError(null); setAttemptId(null)
    Promise.all([
      apiGet<StudentDetail>(`/students/${studentId}`, controller.signal),
      apiGet<Profile>(`/students/${studentId}/profile`, controller.signal),
      apiGet<KnowledgePoint[]>('/knowledge-points', controller.signal),
      apiGet<QuestionCatalogChapter[]>('/questions/catalog', controller.signal),
    ]).then(([studentData, profileData, examData, questionCatalogData]) => {
      if (!controller.signal.aborted) {
        setStudent(studentData); setProfile(profileData); setCatalog(examData); setQuestionCatalog(questionCatalogData)
      }
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
  useEffect(() => {
    setGrade((current) => gradeOptions.includes(current) ? current : gradeOptions[0] ?? '')
  }, [gradeOptions])
  useEffect(() => {
    setChapterCode((current) => visibleChapters.some((item) => item.code === current)
      ? current
      : visibleChapters[0]?.code ?? '')
  }, [visibleChapters])
  return <div className="student-workspace">
    <Space wrap><Link to="/students"><Button icon={<ArrowLeftOutlined />}>学生列表</Button></Link>
      <Tooltip title="刷新画像"><Button icon={<ReloadOutlined />} aria-label="刷新画像" onClick={() => setRefresh((value) => value + 1)} /></Tooltip></Space>
    {error ? <Alert type="error" showIcon message="学生详情加载失败" description={error} /> : !student || !profile ? <Skeleton active /> : <>
      <div className="student-heading"><Space wrap align="center"><Typography.Title level={3}>{student.display_name}</Typography.Title><Tag>{student.student_code}</Tag><Tag color={student.is_demo ? 'purple' : 'blue'}>{student.is_demo ? '演示数据' : '教师添加'}</Tag><Typography.Text type="secondary">{student.class_name}</Typography.Text></Space>
        <Select aria-label="切换学生" value={studentId} style={{ width: 220 }} showSearch filterOption={false} onSearch={setStudentQuery} onChange={(id) => navigate(`/students/${id}`)}
          options={students.map((item) => ({ value: item.id, label: `${item.student_code} ${item.display_name}` }))} /></div>
      <Space wrap className="section-toolbar">
        <Select aria-label="选择画像年级" value={grade || undefined} onChange={setGrade} options={gradeOptions.map((item) => ({ value: item, label: item }))} style={{ width: 180 }} />
        <Select aria-label="选择画像章节" value={chapterCode || undefined} onChange={setChapterCode} options={visibleChapters.map((item) => ({ value: item.code, label: item.name }))} style={{ width: 240 }} />
        <Typography.Text type="secondary">画像与历史作答共用当前年级、章节范围</Typography.Text>
      </Space>
      <Tabs activeKey={tab} onChange={setTab} items={[{ key: 'profile', label: '画像' }, { key: 'attempts', label: '历史作答' }]} />
      {tab === 'profile' && <StudentProfileOverview key={refresh} profile={profile} questionCatalog={questionCatalog} className={student.class_name} grade={grade} chapterCode={chapterCode} />}
      {tab === 'attempts' && <StudentAttemptHistory studentId={studentId} catalog={catalog} chapterCode={chapterCode} onEvidence={setAttemptId} />}
    </>}
    <AttemptEvidenceDrawer studentId={studentId} attemptId={attemptId} onClose={() => setAttemptId(null)} />
  </div>
}
