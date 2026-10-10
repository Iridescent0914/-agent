import { ApartmentOutlined, BarChartOutlined, BookOutlined, CheckSquareOutlined, FileDoneOutlined, TeamOutlined } from '@ant-design/icons'
import { Layout, Menu, Typography } from 'antd'
import type { ReactNode } from 'react'
import { Link, useLocation } from 'react-router-dom'

const { Header, Content } = Layout

export function AppShell({ children }: { children: ReactNode }) {
  const location = useLocation()
  const selected = location.pathname.startsWith('/analytics') ? 'analytics' : location.pathname.startsWith('/grading') || location.pathname.endsWith('/grading') ? 'grading' : location.pathname.startsWith('/classes') ? 'classes' : location.pathname.startsWith('/students') ? 'students' :
    location.pathname.startsWith('/assignments') || location.pathname.startsWith('/paper-instances') ? 'assignments' : 'questions'
  return (
    <Layout className="app-layout">
      <Header className="app-header">
        <Link to="/analytics" className="brand">
          <span className="brand-mark">∑</span>
          <span>
            <Typography.Text className="brand-title">数学教学 Agent</Typography.Text>
            <Typography.Text className="brand-subtitle">有理数 Demo · M6</Typography.Text>
          </span>
        </Link>
        <Menu
          theme="dark"
          mode="horizontal"
          selectedKeys={[selected]}
          items={[
            { key: 'analytics', icon: <BarChartOutlined />, label: <Link to="/analytics">学情</Link> },
            { key: 'questions', icon: <BookOutlined />, label: <Link to="/questions">题库</Link> },
            { key: 'students', icon: <TeamOutlined />, label: <Link to="/students">学生</Link> },
            { key: 'classes', icon: <ApartmentOutlined />, label: <Link to="/classes">班级</Link> },
            { key: 'assignments', icon: <FileDoneOutlined />, label: <Link to="/assignments">组卷</Link> },
            { key: 'grading', icon: <CheckSquareOutlined />, label: <Link to="/grading">批改</Link> },
          ]}
        />
      </Header>
      <Content className="app-content">{children}</Content>
    </Layout>
  )
}
