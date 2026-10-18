import { Alert, Button, Collapse, Empty, Segmented, Select, Skeleton, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useEffect, useMemo, useState } from 'react'
import { apiGet } from '../api/client'
import type { FocusPoint, Mastery, MasteryHistory, Profile } from '../types/profile'
import { MasteryBarChart } from './MasteryBarChart'
import { MasteryHistoryChart } from './MasteryHistoryChart'

const evidenceLabels = { low: '较少', medium: '中等', high: '较多' }
const focusLabels = { practice: '重点练习', recent_errors: '近期关注', observe: '补充证据', diagnostic: '待诊断' }
const focusRank: Record<FocusPoint['category'] | 'tracking', number> = {
  practice: 0,
  recent_errors: 1,
  observe: 2,
  tracking: 3,
  diagnostic: 4,
}

interface PriorityRow {
  mastery: Mastery
  focus?: FocusPoint
}

function recentWrongRate(item: Mastery) {
  return item.recent_attempt_count ? item.recent_wrong_count / item.recent_attempt_count : 0
}

export function StudentProfileOverview({ profile, onEvidence }: { profile: Profile; onEvidence: (id: string) => void }) {
  const [range, setRange] = useState('assessed')
  const assessed = useMemo(() => profile.masteries.filter((item) => item.mastery !== null), [profile.masteries])
  const focusByCode = useMemo(
    () => new Map(profile.focus_points.map((item) => [item.knowledge_point_code, item])),
    [profile.focus_points],
  )
  const priorityRows = useMemo<PriorityRow[]>(() => assessed
    .map((mastery) => ({ mastery, focus: focusByCode.get(mastery.knowledge_point_code) }))
    .sort((left, right) => {
      const leftCategory = left.focus?.category ?? 'tracking'
      const rightCategory = right.focus?.category ?? 'tracking'
      return focusRank[leftCategory] - focusRank[rightCategory]
        || recentWrongRate(right.mastery) - recentWrongRate(left.mastery)
        || (left.mastery.mastery ?? 1) - (right.mastery.mastery ?? 1)
        || left.mastery.knowledge_point_code.localeCompare(right.mastery.knowledge_point_code)
    })
    .slice(0, 5), [assessed, focusByCode])
  const priorityItems = priorityRows.map((item) => item.mastery)
  const [selected, setSelected] = useState(
    priorityItems[0]?.knowledge_point_code ?? assessed[0]?.knowledge_point_code,
  )
  const [history, setHistory] = useState<MasteryHistory | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setHistory(null)
    setError(null)
    if (!selected) return
    const controller = new AbortController()
    apiGet<MasteryHistory>(`/students/${profile.student_id}/mastery/${encodeURIComponent(selected)}/history`, controller.signal)
      .then((data) => { if (!controller.signal.aborted) setHistory(data) })
      .catch((reason: Error) => { if (!controller.signal.aborted) setError(reason.message) })
    return () => controller.abort()
  }, [profile.student_id, selected])

  const items = profile.masteries.filter((item) => range === 'all' || item.attempt_count > 0)
  const current = history?.mastery.knowledge_point_code === selected ? history : null
  const masteryColumns: ColumnsType<Mastery> = [
    {
      title: '知识点',
      dataIndex: 'knowledge_point_name',
      width: 250,
      render: (_, item) => <Button type='link' className='table-link' onClick={() => setSelected(item.knowledge_point_code)}>{item.knowledge_point_name}</Button>,
    },
    {
      title: '掌握度参考值',
      dataIndex: 'mastery',
      width: 125,
      render: (value: number | null) => value === null ? <Typography.Text type='secondary'>未评估</Typography.Text> : value.toFixed(4),
    },
    {
      title: '作答证据量',
      dataIndex: 'confidence_level',
      width: 120,
      render: (_, item) => <Tag color={item.assessment_status === 'assessed' ? 'cyan' : 'default'}>{item.attempt_count ? evidenceLabels[item.confidence_level] : '无证据'}</Tag>,
    },
    { title: '有效 / 不同题', width: 120, render: (_, item) => `${item.attempt_count} 次 / ${item.distinct_question_count} 题` },
    { title: '最近错误', width: 115, render: (_, item) => item.recent_attempt_count ? `${item.recent_wrong_count} / ${item.recent_attempt_count} 次` : '-' },
    { title: '练习难度', width: 110, render: (_, item) => item.practiced_difficulties.join('、') || '-' },
    {
      title: '最近练习',
      dataIndex: 'last_practiced_at',
      width: 135,
      render: (value: string | null) => value ? new Date(value).toLocaleDateString('zh-CN') : '-',
    },
  ]
  const priorityColumns: ColumnsType<PriorityRow> = [
    {
      title: '重点知识点',
      dataIndex: ['mastery', 'knowledge_point_name'],
      width: 220,
      render: (_, item) => <Button type='link' className='table-link' onClick={() => setSelected(item.mastery.knowledge_point_code)}>{item.mastery.knowledge_point_name}</Button>,
    },
    {
      title: '诊断',
      width: 105,
      render: (_, item) => {
        const category = item.focus?.category ?? 'tracking'
        const color = category === 'practice' ? 'volcano' : category === 'recent_errors' ? 'gold' : 'default'
        return <Tag color={color}>{category === 'tracking' ? '持续跟踪' : focusLabels[category]}</Tag>
      },
    },
    {
      title: '参考值',
      width: 90,
      render: (_, item) => item.mastery.mastery?.toFixed(4) ?? '-',
    },
    {
      title: '诊断依据',
      render: (_, item) => item.focus?.reason
        ?? `当前有 ${item.mastery.attempt_count} 次确认作答，最近 ${item.mastery.recent_attempt_count} 次错 ${item.mastery.recent_wrong_count} 次。`,
    },
  ]

  return <div className='profile-overview'>
    <section className='profile-section' aria-labelledby='priority-heading'>
      <div className='section-heading'>
        <div>
          <Typography.Title id='priority-heading' level={4}>重点知识点诊断</Typography.Title>
          <Typography.Text type='secondary'>按薄弱程度、近期错误和证据充分度，优先展示最多 5 个知识点。</Typography.Text>
        </div>
        <Tag color='blue'>{priorityItems.length} 个重点</Tag>
      </div>
      <MasteryBarChart items={priorityItems} onSelect={setSelected} label='重点知识点掌握度条形图' />
      <Table
        className='priority-table'
        size='small'
        rowKey={(item) => item.mastery.knowledge_point_code}
        columns={priorityColumns}
        dataSource={priorityRows}
        pagination={false}
        scroll={{ x: 720 }}
        locale={{ emptyText: <Empty description='尚无已评估知识点' /> }}
      />
    </section>

    <section className='profile-section' aria-labelledby='history-heading'>
      <div className='section-heading'>
        <div>
          <Typography.Title id='history-heading' level={4}>重点知识点掌握度变化</Typography.Title>
          <Typography.Text type='secondary'>点击上方知识点或切换选择，只复用这一张趋势图。</Typography.Text>
        </div>
        <Select
          aria-label='曲线知识点'
          className='knowledge-point-select'
          value={selected}
          onChange={setSelected}
          showSearch
          optionFilterProp='label'
          options={assessed.map((item) => ({ value: item.knowledge_point_code, label: item.knowledge_point_name }))}
        />
      </div>
      {!selected ? <Empty description='尚无可展示趋势的已评估知识点' /> : error ? <Alert type='error' message={error} /> : !current ? <Skeleton active /> : <>
        <Space wrap>
          <Typography.Text strong>{current.mastery.knowledge_point_name}</Typography.Text>
          <Tag>{current.mastery.attempt_count} 次确认作答</Tag>
          <Tag>{current.mastery.distinct_question_count} 道不同题</Tag>
          <Typography.Text type='secondary'>题型：{current.mastery.practiced_question_types.map((type) => type === 'choice' ? '选择' : '填空').join('、') || '未评估'}</Typography.Text>
          <Typography.Text type='secondary'>练习范围：{current.mastery.first_practiced_at ? `${new Date(current.mastery.first_practiced_at).toLocaleDateString('zh-CN')} 至 ${new Date(current.mastery.last_practiced_at!).toLocaleDateString('zh-CN')}` : '未评估'}</Typography.Text>
        </Space>
        <MasteryHistoryChart history={current} onEvidence={onEvidence} />
        <Collapse
          ghost
          className='profile-history-details'
          items={[{
            key: 'attempts',
            label: `查看该知识点的逐次作答（${current.points.length}）`,
            children: <Table
              size='small'
              rowKey='attempt_id'
              dataSource={current.points}
              pagination={{ pageSize: 5, showSizeChanger: false }}
              scroll={{ x: 610 }}
              columns={[
                { title: '作答时间', dataIndex: 'answered_at', render: (value: string) => new Date(value).toLocaleString('zh-CN') },
                { title: '结果', dataIndex: 'is_correct', render: (value: boolean) => value ? '正确' : '错误' },
                { title: '掌握度变化', render: (_, point) => `${point.mastery_before.toFixed(4)} → ${point.mastery_after.toFixed(4)}` },
                { title: '修订', dataIndex: 'revision_no' },
                { title: '证据', render: (_, point) => <Button type='link' onClick={() => onEvidence(point.attempt_id)}>查看作答</Button> },
              ]}
            />,
          }]}
        />
      </>}
    </section>

    <Collapse
      className='profile-details-collapse'
      items={[{
        key: 'full-profile',
        label: <Space wrap><Typography.Text strong>完整画像与作答证据</Typography.Text><Tag>{profile.masteries.length} 个知识点</Tag><Tag>{profile.recent_errors.length} 条近期错误</Tag></Space>,
        children: <Space direction='vertical' size='large' className='full-width'>
          <div>
            <div className='section-heading'>
              <Typography.Title level={5}>全部知识点</Typography.Title>
              <Segmented
                value={range}
                onChange={setRange}
                options={[
                  { label: '已有证据', value: 'assessed' },
                  { label: '全部（含未评估）', value: 'all' },
                ]}
              />
            </div>
            <Table
              rowKey='knowledge_point_code'
              columns={masteryColumns}
              dataSource={items}
              scroll={{ x: 1070 }}
              pagination={{ pageSize: 10, showSizeChanger: false, showTotal: (total) => `共 ${total} 个知识点` }}
              locale={{ emptyText: <Empty description='尚无已评估知识点' /> }}
            />
          </div>
          <div>
            <Typography.Title level={5}>最近错误作答</Typography.Title>
            <Table
              rowKey='attempt_id'
              dataSource={profile.recent_errors}
              pagination={{ pageSize: 5, showSizeChanger: false }}
              scroll={{ x: 650 }}
              columns={[
                { title: '时间', dataIndex: 'answered_at', render: (value: string) => new Date(value).toLocaleString('zh-CN') },
                { title: '原题编号', dataIndex: 'original_qid', render: (_, point) => <Button type='link' className='table-link' onClick={() => onEvidence(point.attempt_id)}>{point.original_qid}</Button> },
                { title: '主要知识点', dataIndex: 'knowledge_point_name' },
                { title: '修订', dataIndex: 'revision_no' },
              ]}
              locale={{ emptyText: <Empty description='暂无确认错误作答' /> }}
            />
          </div>
        </Space>,
      }]}
    />
  </div>
}
