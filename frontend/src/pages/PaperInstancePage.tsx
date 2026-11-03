import { ArrowDownOutlined, ArrowLeftOutlined, ArrowUpOutlined, CameraOutlined, CheckSquareOutlined, DeleteOutlined, EditOutlined, PrinterOutlined, RedoOutlined, StopOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Descriptions, Drawer, List, Modal, Space, Tag, Typography, message } from 'antd'
import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { apiDelete, apiGet, apiPost, apiPut } from '../api/client'
import { AnswerView } from '../components/AnswerView'
import { RichQuestionText } from '../components/RichQuestionText'
import { QuestionOptions } from '../components/QuestionOptions'
import type { Alternatives, PaperInstance, PaperItem } from '../types/assignment'

const sourceModuleLabels: Record<string, string> = {
  required: '必做题',
  personalized: '个性化',
  history: '历史回顾',
  weekly_summary: '周总结',
  legacy_recommendation: '历史推荐',
}

export function PaperInstancePage() {
  const { paperInstanceId } = useParams()
  const navigate = useNavigate()
  const [paper, setPaper] = useState<PaperInstance | null>(null)
  const [alternatives, setAlternatives] = useState<Alternatives | null>(null)
  const [target, setTarget] = useState<PaperItem | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const load = () => {
    if (!paperInstanceId) return
    setLoading(true); setError(null)
    apiGet<PaperInstance>(`/paper-instances/${paperInstanceId}`).then(setPaper)
      .catch((reason) => setError((reason as Error).message)).finally(() => setLoading(false))
  }
  useEffect(load, [paperInstanceId])
  const update = async (work: () => Promise<PaperInstance>) => {
    setLoading(true); setError(null)
    try { setPaper(await work()) }
    catch (reason) { setError((reason as Error).message) }
    finally { setLoading(false) }
  }
  const openAlternatives = async (item: PaperItem) => {
    if (!paper) return
    setTarget(item); setAlternatives(null)
    try { setAlternatives(await apiGet<Alternatives>(`/paper-instances/${paper.id}/items/${item.id}/alternatives`)) }
    catch (reason) { setError((reason as Error).message); setTarget(null) }
  }
  const replace = (questionId: string) => {
    if (!paper || !target) return
    update(() => apiPost<PaperInstance>(`/paper-instances/${paper.id}/items/${target.id}/replace`, { replacement_question_id: questionId, expected_edit_revision: paper.edit_revision }))
      .then(() => { setTarget(null); setAlternatives(null); message.success('换题成功') })
  }
  const remove = (item: PaperItem) => {
    if (!paper) return
    Modal.confirm({ title: `删除第 ${item.order_no} 题？`, content: '删除后题号会自动连续重排。', okText: '删除', okButtonProps: { danger: true },
      onOk: () => update(() => apiDelete<PaperInstance>(`/paper-instances/${paper.id}/items/${item.id}`, { expected_edit_revision: paper.edit_revision })) })
  }
  const move = (item: PaperItem, direction: -1 | 1) => {
    if (!paper) return
    const ids = paper.items.map((value) => value.id)
    const index = ids.indexOf(item.id); const next = index + direction
    if (next < 0 || next >= ids.length) return
    ;[ids[index], ids[next]] = [ids[next], ids[index]]
    update(() => apiPut<PaperInstance>(`/paper-instances/${paper.id}/order`, { item_ids: ids, expected_edit_revision: paper.edit_revision }))
  }
  const publish = () => {
    if (!paper) return
    const short = paper.item_count < (paper.requested_count ?? paper.item_count)
    Modal.confirm({ title: '确认发布这份试卷？', content: short ? '当前题量少于目标题量，将记录教师确认。' : '发布后内容不可修改。',
      onOk: () => update(() => apiPost<PaperInstance>(`/paper-instances/${paper.id}/publish`, { expected_edit_revision: paper.edit_revision, acknowledge_shortfall: short, acknowledgement_reason: short ? '教师确认当前题量' : null })) })
  }
  const deleteDraft = () => {
    if (!paper) return
    Modal.confirm({
      title: '删除整份草稿？',
      content: '草稿及其中题目将被永久删除；已发布版本不受影响。',
      okText: '删除草稿', okButtonProps: { danger: true },
      onOk: async () => {
        await apiDelete<{ deleted_id: string }>(`/paper-instances/${paper.id}`, {
          expected_edit_revision: paper.edit_revision,
        })
        message.success('草稿已删除')
        navigate(`/assignments/${paper.assignment_id}`)
      },
    })
  }
  const withdraw = () => {
    if (!paper) return
    Modal.confirm({
      title: '撤回这份已发布试卷？',
      content: '撤回后实例码仍可查询历史，但该版本不能再用于新的答题或批改。',
      okText: '确认撤回', okButtonProps: { danger: true },
      onOk: () => update(() => apiPost<PaperInstance>(
        `/paper-instances/${paper.id}/withdraw`,
        { expected_edit_revision: paper.edit_revision, reason: '教师主动撤回已发布试卷' },
      )).then(() => message.success('已发布试卷已撤回')),
    })
  }
  const republish = () => {
    if (!paper) return
    Modal.confirm({
      title: '重新发布这份试卷？',
      content: '试卷内容和原实例码保持不变，重新发布后恢复为当前有效版本。',
      okText: '确认重新发布',
      onOk: () => update(() => apiPost<PaperInstance>(
        `/paper-instances/${paper.id}/republish`,
        { expected_edit_revision: paper.edit_revision },
      )).then(() => message.success('试卷已重新发布')),
    })
  }
  const revise = async () => {
    if (!paper) return
    try { const draft = await apiPost<PaperInstance>(`/paper-instances/${paper.id}/revise`, {}); navigate(`/paper-instances/${draft.id}`) }
    catch (reason) { setError((reason as Error).message) }
  }
  return <Space direction="vertical" size="large" className="page-stack paper-workspace">
    {paper && <Button type="link" icon={<ArrowLeftOutlined />}><Link to={`/assignments/${paper.assignment_id}`}>返回作业</Link></Button>}
    {error && <Alert type="error" showIcon message="操作失败" description={error} action={<Button onClick={load}>刷新</Button>} />}
    <div className="student-heading">
      <div><Typography.Title level={2}>{paper ? `${paper.student_code} · ${paper.student_name} · v${paper.version_no}` : '试卷'}</Typography.Title><Typography.Text type="secondary">{paper?.status === 'draft' ? '未发布草稿，可调整题目' : paper?.is_active ? `有效发布 · ${paper.instance_code}` : paper?.invalidation_type === 'superseded' ? `已被更新 · ${paper.instance_code}` : `已撤回 · ${paper?.instance_code}`}</Typography.Text></div>
      {paper?.status === 'draft' ? <Space><Button danger icon={<DeleteOutlined />} onClick={deleteDraft}>删除草稿</Button><Button type="primary" onClick={publish} loading={loading}>发布试卷</Button></Space> : <Space wrap>{paper && <><Button icon={<PrinterOutlined />}><Link to={`/paper-instances/${paper.id}/print`}>学生版打印</Link></Button><Button icon={<CameraOutlined />}><Link to={`/paper-instances/${paper.id}/recognize`}>上传答题照片</Link></Button><Button icon={<CheckSquareOutlined />}><Link to={`/paper-instances/${paper.id}/grading`}>录入 / 查看批改</Link></Button></>}{paper?.is_active && <Button danger icon={<StopOutlined />} onClick={withdraw}>撤回发布</Button>}{paper?.invalidation_type === 'superseded' ? paper.superseded_by_instance_id && <Button><Link to={`/paper-instances/${paper.superseded_by_instance_id}`}>查看新版本</Link></Button> : <>{paper?.invalidation_type === 'withdrawn' && <Button type="primary" icon={<RedoOutlined />} onClick={republish}>重新发布</Button>}<Button icon={<EditOutlined />} onClick={revise}>创建修订版</Button></>}</Space>}
    </div>
    {paper?.invalidated_at && <Alert type="warning" showIcon message={paper.invalidation_type === 'superseded' ? '此版本已被更新' : '此版本已撤回'} description={<Space direction="vertical"><span>{paper.invalidation_reason}</span>{paper.superseded_by_instance_id && <Link to={`/paper-instances/${paper.superseded_by_instance_id}`}>查看当前新版本</Link>}</Space>} />}
    {paper && <Descriptions bordered size="small" column={{ xs: 1, sm: 2, md: 4 }}><Descriptions.Item label="状态"><Tag color={paper.status === 'draft' ? 'gold' : paper.is_active ? 'green' : 'red'}>{paper.status === 'draft' ? '草稿' : paper.is_active ? '有效发布' : paper.invalidation_type === 'superseded' ? '已被更新' : '已撤回'}</Tag></Descriptions.Item><Descriptions.Item label="题量">{paper.item_count} / {paper.requested_count ?? paper.item_count}</Descriptions.Item><Descriptions.Item label="编辑修订">{paper.edit_revision}</Descriptions.Item><Descriptions.Item label="实例码">{paper.instance_code ?? '发布时生成'}</Descriptions.Item></Descriptions>}
    {paper?.items.map((item, index) => <Card key={item.id} loading={loading} title={`第 ${item.order_no} 题 · ${item.question_snapshot.original_qid ?? ''}`} extra={paper.status === 'draft' && <Space>
      <Button aria-label={`上移第${item.order_no}题`} icon={<ArrowUpOutlined />} disabled={index === 0} onClick={() => move(item, -1)} />
      <Button aria-label={`下移第${item.order_no}题`} icon={<ArrowDownOutlined />} disabled={index === paper.items.length - 1} onClick={() => move(item, 1)} />
      <Button onClick={() => openAlternatives(item)}>换题</Button><Button danger icon={<DeleteOutlined />} onClick={() => remove(item)}>删除</Button>
    </Space>}>
      <Space wrap><Tag>{item.question_snapshot.question_type === 'choice' ? '选择题' : item.question_snapshot.question_type === 'true_false' ? '判断题' : '填空题'}</Tag><Tag>难度 {item.question_snapshot.difficulty}</Tag><Tag color={item.selection_audit?.source_module === 'personalized' ? 'purple' : item.selection_audit?.bucket === 'new' ? 'cyan' : 'gold'}>{sourceModuleLabels[item.selection_audit?.source_module ?? ''] ?? (item.selection_audit?.bucket === 'new' ? '新知识' : '回顾')}</Tag><Tag>{item.knowledge_point_snapshot.name}</Tag></Space>
      <RichQuestionText text={item.question_snapshot.question_text ?? ''} assets={item.assets} />
      {item.options_snapshot != null && <QuestionOptions options={item.options_snapshot} assets={item.assets} />}
      <Typography.Paragraph type="secondary">推荐理由：{item.selection_audit?.reason_text ?? '来自历史版本快照'}</Typography.Paragraph>
      {item.selection_audit?.personalized_audit && <Descriptions size="small" bordered column={{ xs: 1, sm: 2, md: 4 }}>
        <Descriptions.Item label="生成依据">{item.selection_audit.personalized_audit.literacy_name} {item.selection_audit.personalized_audit.literacy_score} 分</Descriptions.Item>
        <Descriptions.Item label="星级题位">基础 {item.selection_audit.personalized_audit.base_star} 星 · 第 {item.selection_audit.personalized_audit.slot_no} 题目标 {item.selection_audit.personalized_audit.target_star} 星</Descriptions.Item>
        <Descriptions.Item label="规则版本">{item.selection_audit.personalized_audit.rule_version}</Descriptions.Item>
        <Descriptions.Item label="生成时间">{new Date(item.selection_audit.personalized_audit.generated_at).toLocaleString('zh-CN')}</Descriptions.Item>
      </Descriptions>}
      <details><summary>教师答案与判分规则</summary><AnswerView answer={item.answer_snapshot} /><pre className="json-block">{JSON.stringify(item.answer_rule_snapshot, null, 2)}</pre></details>
    </Card>)}
    <Drawer title={`替换第 ${target?.order_no ?? ''} 题`} width={720} open={Boolean(target)} onClose={() => { setTarget(null); setAlternatives(null) }}>
      <List loading={!alternatives} dataSource={alternatives?.items ?? []} locale={{ emptyText: '当前约束下没有可替换题目' }} renderItem={(item) => <List.Item actions={[<Button key="select" type="primary" onClick={() => replace(item.question_id)}>选用</Button>]}><List.Item.Meta title={`${item.original_qid} · 难度 ${item.difficulty}`} description={<><div>{item.knowledge_point_name}</div><div>{item.reason_text}</div></>} /></List.Item>} />
    </Drawer>
  </Space>
}
