import { Empty } from 'antd'
import { useMemo } from 'react'
import type { MasteryHistory } from '../types/profile'
import { ProfileChart } from './ProfileChart'

export function MasteryHistoryChart({ history, onEvidence }: { history: MasteryHistory; onEvidence: (id: string) => void }) {
  const option = useMemo(() => ({
    animation: false,
    grid: { left: 40, right: 24, top: 24, bottom: 48 },
    tooltip: { trigger: 'item', renderMode: 'richText', confine: true,
      formatter: (value: unknown) => {
        const point = history.points[(value as { dataIndex: number }).dataIndex]
        return point ? `${new Date(point.answered_at).toLocaleString('zh-CN')}\n修订 ${point.revision_no} · ${point.is_correct ? '正确' : '错误'}\n${point.mastery_before.toFixed(4)} → ${point.mastery_after.toFixed(4)}` : ''
      } },
    xAxis: { type: 'category', data: history.points.map((point, index) => `${index + 1}\n${new Date(point.answered_at).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })}`) },
    yAxis: { type: 'value', min: 0, max: 1, interval: 0.2 },
    series: [{ type: 'line', name: '掌握度参考值', symbolSize: 12, lineStyle: { color: '#168779', width: 2 },
      data: history.points.map((point) => ({ value: point.mastery_after, itemStyle: { color: point.is_correct ? '#168779' : '#ce6550' } })) }],
  }), [history])
  if (!history.points.length) return <Empty description="此知识点尚无确认作答" />
  return <ProfileChart option={option} height={280} label="掌握度变化曲线"
    onPoint={(index) => { if (history.points[index]) onEvidence(history.points[index].attempt_id) }} />
}
