import { Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { QuestionDetailPage } from './pages/QuestionDetailPage'
import { QuestionListPage } from './pages/QuestionListPage'
import { StudentDetailPage } from './pages/StudentDetailPage'
import { StudentListPage } from './pages/StudentListPage'

export default function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<Navigate to="/questions" replace />} />
        <Route path="/questions" element={<QuestionListPage />} />
        <Route path="/questions/:questionId" element={<QuestionDetailPage />} />
        <Route path="/students" element={<StudentListPage />} />
        <Route path="/students/:studentId" element={<StudentDetailPage />} />
        <Route path="*" element={<Navigate to="/questions" replace />} />
      </Routes>
    </AppShell>
  )
}
