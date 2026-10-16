import { Empty } from 'antd'
import { useMemo } from 'react'
import type { Mastery } from '../types/profile'
import { ProfileChart } from './ProfileChart'

export function MasteryBarChart({ items, onSelect, label = '知识点掌握度条形图' }: { items: Mastery[]; onSelect: (code: string) => void; label?: string }) {
  const assessed = useMemo(() => items.filter((item) => item.mastery !== null), [items])
  const option = useMemo(() => ({
    animation: false,
    grid: { left: 156, right: 46, top: 16, bottom: 32 },
    tooltip: { trigger: 'axis', renderMode: 'richText', confine: true },
    xAxis: { type: 'value', min: 0, max: 1, interval: 0.2 },
    yAxis: { type: 'category', inverse: true, data: assessed.map((item) => item.exam_point_name),
      axisLabel: { width: 136, overflow: 'truncate', fontSize: 12 }, axisTick: { show: false } },
    series: [{ name: '掌握度参考值', type: 'bar', barMaxWidth: 20,
      label: { show: true, position: 'right', formatter: '{c}' },
      data: assessed.map((item) => ({ value: item.mastery,
        itemStyle: { color: item.assessment_status === 'insufficient' ? '#9ca3af' : (item.mastery ?? 0) < 0.4 ? '#ce6550' : '#168779' } })) }],
  }), [assessed])
  if (!assessed.length) return <Empty description="暂无已确认的作答证据" />
  return <ProfileChart option={option} height={Math.max(240, assessed.length * 42 + 48)} label={label}
    onPoint={(index) => { if (assessed[index]) onSelect(assessed[index].exam_point_code) }} />
}
