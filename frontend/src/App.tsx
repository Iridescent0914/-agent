import { Skeleton } from 'antd'
import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from './components/AppShell'

const ClassAnalyticsPage = lazy(() => import('./pages/ClassAnalyticsPage').then((module) => ({ default: module.ClassAnalyticsPage })))
const AssignmentCreatePage = lazy(() => import('./pages/AssignmentCreatePage').then((module) => ({ default: module.AssignmentCreatePage })))
const AssignmentDetailPage = lazy(() => import('./pages/AssignmentDetailPage').then((module) => ({ default: module.AssignmentDetailPage })))
const AssignmentListPage = lazy(() => import('./pages/AssignmentListPage').then((module) => ({ default: module.AssignmentListPage })))
const PaperInstancePage = lazy(() => import('./pages/PaperInstancePage').then((module) => ({ default: module.PaperInstancePage })))
const PaperPrintPage = lazy(() => import('./pages/PaperPrintPage').then((module) => ({ default: module.PaperPrintPage })))
const RecognitionPage = lazy(() => import('./pages/RecognitionPage').then((module) => ({ default: module.RecognitionPage })))
const GradingQueuePage = lazy(() => import('./pages/GradingQueuePage').then((module) => ({ default: module.GradingQueuePage })))
const PaperGradingPage = lazy(() => import('./pages/PaperGradingPage').then((module) => ({ default: module.PaperGradingPage })))
const QuestionDetailPage = lazy(() => import('./pages/QuestionDetailPage').then((module) => ({ default: module.QuestionDetailPage })))
const QuestionListPage = lazy(() => import('./pages/QuestionListPage').then((module) => ({ default: module.QuestionListPage })))
const StudentDetailPage = lazy(() => import('./pages/StudentDetailPage').then((module) => ({ default: module.StudentDetailPage })))
const StudentListPage = lazy(() => import('./pages/StudentListPage').then((module) => ({ default: module.StudentListPage })))

export default function App() {
  return (
    <AppShell>
      <Suspense fallback={<Skeleton active paragraph={{ rows: 10 }} />}>
        <Routes>
          <Route path="/" element={<Navigate to="/analytics" replace />} />
          <Route path="/analytics" element={<ClassAnalyticsPage />} />
          <Route path="/questions" element={<QuestionListPage />} />
          <Route path="/questions/:questionId" element={<QuestionDetailPage />} />
          <Route path="/students" element={<StudentListPage />} />
          <Route path="/students/:studentId" element={<StudentDetailPage />} />
          <Route path="/assignments" element={<AssignmentListPage />} />
          <Route path="/assignments/new" element={<AssignmentCreatePage />} />
          <Route path="/assignments/:assignmentId" element={<AssignmentDetailPage />} />
          <Route path="/paper-instances/:paperInstanceId/print" element={<PaperPrintPage />} />
          <Route path="/paper-instances/:paperInstanceId/recognize" element={<RecognitionPage />} />
          <Route path="/paper-instances/:paperInstanceId" element={<PaperInstancePage />} />
          <Route path="/grading" element={<GradingQueuePage />} />
          <Route path="/paper-instances/:paperInstanceId/grading" element={<PaperGradingPage />} />
          <Route path="*" element={<Navigate to="/analytics" replace />} />
        </Routes>
      </Suspense>
    </AppShell>
  )
}
