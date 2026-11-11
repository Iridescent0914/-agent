import { Alert, Empty, Switch, Typography } from 'antd'
import { useMemo, useState } from 'react'
import type { LiteracyScore } from '../types/profile'
import { ProfileChart } from './ProfileChart'

export function LiteracyRadarChart({
  student,
  classAverage,
  seriesName = '学生',
  label = '核心素养能力雷达图',
}: {
  student: LiteracyScore[]
  classAverage?: LiteracyScore[]
  seriesName?: string
  label?: string
}) {
  const [showClass, setShowClass] = useState(true)
  const names = useMemo(() => student.map((item) => item.name), [student])
  const classByName = useMemo(
    () => new Map((classAverage ?? []).map((item) => [item.name, item.score])),
    [classAverage],
  )
  const classComplete = Boolean(classAverage?.length)
    && classAverage?.length === names.length
    && new Set(classAverage?.map((item) => item.name)).size === names.length
    && names.every((name) => classByName.has(name))
    && (classAverage ?? []).every((item) => item.rule_version === student[0]?.rule_version)
  const displayClass = showClass && classComplete
  const option = useMemo(() => ({
    animation: false,
    tooltip: { trigger: 'item' },
    legend: { bottom: 0, data: displayClass ? [seriesName, '班级平均'] : [seriesName] },
    radar: {
      radius: '62%',
      indicator: names.map((name) => ({ name, max: 100 })),
      axisName: { color: '#344054', fontSize: 12 },
      splitArea: { areaStyle: { color: ['#fbfdff', '#f4f8fb'] } },
    },
    series: [{
      type: 'radar',
      data: [
        {
          name: seriesName,
          value: student.map((item) => item.score),
          lineStyle: { color: '#176c61', width: 2 },
          itemStyle: { color: '#176c61' },
          areaStyle: { color: 'rgba(23, 135, 121, .18)' },
        },
        ...(displayClass ? [{
          name: '班级平均',
          value: names.map((name) => classByName.get(name)),
          lineStyle: { color: '#315b8a', type: 'dashed', width: 2 },
          itemStyle: { color: '#315b8a' },
          areaStyle: { color: 'rgba(49, 91, 138, .08)' },
        }] : []),
      ],
    }],
  }), [classByName, displayClass, names, seriesName, student])

  if (!student.length) return <Empty description="暂无素养能力数据" />
  return <div>
    <div className="section-heading">
      <Typography.Text type="secondary">
        {student[0]?.score_source === 'baseline' ? '当前显示基础值，动态评分算法尚未接入。' : '根据已确认作答计算；没有作答证据的素养从 60 分开始，不代表已测得的能力。'}
      </Typography.Text>
      {classComplete ? <Switch checked={showClass} onChange={setShowClass} checkedChildren="显示班级平均" unCheckedChildren="隐藏班级平均" /> : null}
    </div>
    {classAverage?.length && !classComplete ? <Alert type="warning" showIcon message="班级平均的素养维度或版本不一致，请重新加载班级数据。" /> : null}
    <ProfileChart option={option} height={390} label={label} onPoint={() => undefined} />
  </div>
}
