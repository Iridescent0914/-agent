import { ArrowLeftOutlined, DeleteOutlined, RedoOutlined, ReloadOutlined, StopOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Modal, Space, Table, Tag, Typography, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { apiDelete, apiGet, apiPost } from '../api/client'
import type { AssignmentDetail, PaperSummary } from '../types/assignment'

export function AssignmentDetailPage() {
  const { assignmentId } = useParams()
  const [data, setData] = useState<AssignmentDetail | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const load = () => {
    if (!assignmentId) return
    setLoading(true); setError(null)
    apiGet<AssignmentDetail>(`/assignments/${assignmentId}`)
      .then((value) => { setData(value); setSelected([]) })
      .catch((reason) => setError((reason as Error).message)).finally(() => setLoading(false))
  }
  useEffect(load, [assignmentId])
  const publishSelected = () => {
    if (!assignmentId || !data) return
    const targets = data.papers.filter((paper) => selected.includes(paper.id))
    const hasShortfall = targets.some((paper) => paper.item_count < (paper.requested_count ?? paper.item_count))
    Modal.confirm({
      title: `发布 ${targets.length} 份试卷？`,
      content: hasShortfall ? '包含题量不足或教师删题的试卷，本次将记录教师确认。' : '发布后题目与判分快照不可修改。',
      okText: '确认发布', cancelText: '取消',
      onOk: async () => {
        setLoading(true)
        try {
          const updated = await apiPost<AssignmentDetail>(`/assignments/${assignmentId}/publish`, {
            papers: targets.map((paper) => ({
              paper_instance_id: paper.id, expected_edit_revision: paper.edit_revision,
              acknowledge_shortfall: paper.item_count < (paper.requested_count ?? paper.item_count),
              acknowledgement_reason: paper.item_count < (paper.requested_count ?? paper.item_count) ? '教师在批量发布时确认当前题量' : null,
            })),
          })
          setData(updated); setSelected([]); message.success('发布成功')
        } catch (reason) { setError((reason as Error).message) }
        finally { setLoading(false) }
      },
    })
  }
  const deleteDraft = (paper: PaperSummary) => {
    Modal.confirm({
      title: `删除 ${paper.student_name} 的 v${paper.version_no} 草稿？`,
      content: '草稿及其中题目将被永久删除，已发布版本不受影响。',
      okText: '删除草稿', cancelText: '取消', okButtonProps: { danger: true },
      onOk: async () => {
        setLoading(true); setError(null)
        try {
          await apiDelete<{ deleted_id: string }>(`/paper-instances/${paper.id}`, {
            expected_edit_revision: paper.edit_revision,
          })
          message.success('草稿已删除')
          load()
        } catch (reason) {
          setError((reason as Error).message); setLoading(false)
        }
      },
    })
  }
  const withdrawPaper = (paper: PaperSummary) => {
    Modal.confirm({
      title: `撤回 ${paper.student_name} 的 v${paper.version_no} 试卷？`,
      content: '撤回后实例码仍可查询历史，但该版本不再作为有效发布版本。',
      okText: '确认撤回', cancelText: '取消', okButtonProps: { danger: true },
      onOk: async () => {
        setLoading(true); setError(null)
        try {
          await apiPost(`/paper-instances/${paper.id}/withdraw`, {
            expected_edit_revision: paper.edit_revision,
            reason: '教师从作业详情页主动撤回已发布试卷',
          })
          message.success('已发布试卷已撤回')
          load()
        } catch (reason) {
          setError((reason as Error).message); setLoading(false)
        }
      },
    })
  }
  const republishPaper = (paper: PaperSummary) => {
    Modal.confirm({
      title: `重新发布 ${paper.student_name} 的 v${paper.version_no} 试卷？`,
      content: '试卷内容和原实例码保持不变，重新发布后恢复为当前有效版本。',
      okText: '确认重新发布', cancelText: '取消',
      onOk: async () => {
        setLoading(true); setError(null)
        try {
          await apiPost(`/paper-instances/${paper.id}/republish`, {
            expected_edit_revision: paper.edit_revision,
          })
          message.success('试卷已重新发布')
          load()
        } catch (reason) {
          setError((reason as Error).message); setLoading(false)
        }
      },
    })
  }
  const columns: ColumnsType<PaperSummary> = [
    { title: '学生', render: (_, row) => `${row.student_code} · ${row.student_name}` },
    { title: '版本', dataIndex: 'version_no', width: 80, render: (value) => `v${value}` },
    { title: '状态', width: 120, render: (_, row) => <Tag color={row.status === 'draft' ? 'gold' : row.is_active ? 'green' : 'red'}>{row.status === 'draft' ? '草稿' : row.is_active ? '有效发布' : row.invalidation_type === 'superseded' ? '已被更新' : row.invalidation_type === 'withdrawn' ? '已撤回' : '已失效'}</Tag> },
    { title: '题量', width: 110, render: (_, row) => `${row.item_count} / ${row.requested_count ?? row.item_count}` },
    { title: '实例码', dataIndex: 'instance_code', width: 160, render: (value) => value || '—' },
    {
      title: '操作', width: 270, fixed: 'right',
      render: (_, row) => <Space>
        <Link to={`/paper-instances/${row.id}`}>{row.status === 'draft' ? '编辑' : '查看'}</Link>
        {row.status === 'published' && <Link to={`/paper-instances/${row.id}/grading`}>批改</Link>}
        {row.status === 'draft' && <Button type='link' danger size='small' icon={<DeleteOutlined />} onClick={() => deleteDraft(row)}>删除草稿</Button>}
        {row.status === 'published' && row.is_active && <Button type='link' danger size='small' icon={<StopOutlined />} onClick={() => withdrawPaper(row)}>撤回</Button>}
        {row.status === 'published' && row.invalidation_type === 'withdrawn' && <Button type='link' size='small' icon={<RedoOutlined />} onClick={() => republishPaper(row)}>重新发布</Button>}
      </Space>,
    },
  ]
  return <Space direction="vertical" size="large" className="page-stack assignment-workspace">
    <div><Button type="link" icon={<ArrowLeftOutlined />}><Link to="/assignments">返回作业列表</Link></Button></div>
    {error && <Alert type="error" showIcon message="操作失败" description={error} />}
    <div className="student-heading">
      <div><Typography.Title level={2}>{data?.title ?? '作业详情'}</Typography.Title><Typography.Text type="secondary">草稿可删除；新版发布后旧版自动作废，但保留审计快照</Typography.Text></div>
      <Space><Button icon={<ReloadOutlined />} onClick={load}>刷新</Button><Button type="primary" disabled={!selected.length} onClick={publishSelected}>发布所选草稿</Button></Space>
    </div>
    <Card className="table-card">
      <Table rowKey="id" loading={loading} columns={columns} dataSource={data?.papers ?? []} pagination={false} scroll={{ x: 820 }}
        rowSelection={{ selectedRowKeys: selected, onChange: (keys) => setSelected(keys.map(String)), getCheckboxProps: (row) => ({ disabled: row.status !== 'draft' }) }} />
    </Card>
  </Space>
}
