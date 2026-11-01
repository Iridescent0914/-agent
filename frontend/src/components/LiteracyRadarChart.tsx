import { Empty, Switch, Typography } from 'antd'
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
  const option = useMemo(() => ({
    animation: false,
    tooltip: { trigger: 'item' },
    legend: { bottom: 0, data: showClass && classAverage?.length ? [seriesName, '班级平均'] : [seriesName] },
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
        ...(showClass && classAverage?.length ? [{
          name: '班级平均',
          value: names.map((name) => classByName.get(name) ?? 0),
          lineStyle: { color: '#315b8a', type: 'dashed', width: 2 },
          itemStyle: { color: '#315b8a' },
          areaStyle: { color: 'rgba(49, 91, 138, .08)' },
        }] : []),
      ],
    }],
  }), [classAverage, classByName, names, seriesName, showClass, student])

  if (!student.length) return <Empty description="暂无素养能力数据" />
  return <div>
    <div className="section-heading">
      <Typography.Text type="secondary">
        {student[0]?.score_source === 'baseline' ? '当前显示基础值，动态评分算法尚未接入。' : '根据已确认作答计算。'}
      </Typography.Text>
      {classAverage?.length ? <Switch checked={showClass} onChange={setShowClass} checkedChildren="显示班级平均" unCheckedChildren="隐藏班级平均" /> : null}
    </div>
    <ProfileChart option={option} height={390} label={label} onPoint={() => undefined} />
  </div>
}
