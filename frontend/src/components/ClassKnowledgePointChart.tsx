import { Empty } from 'antd'
import { useMemo } from 'react'
import type { ClassKnowledgePointAnalytics } from '../types/analytics'
import { ProfileChart } from './ProfileChart'

export function ClassKnowledgePointChart({
  items,
  metric,
  onSelect,
}: {
  items: ClassKnowledgePointAnalytics[]
  metric: 'accuracy' | 'mastery'
  onSelect: (code: string) => void
}) {
  const available = useMemo(
    () => items.filter((item) => (
      metric === 'accuracy' ? item.accuracy !== null : item.average_mastery !== null
    )),
    [items, metric],
  )
  const option = useMemo(() => {
    const accuracy = metric === 'accuracy'
    return {
      animation: false,
      grid: { left: 170, right: 72, top: 18, bottom: 38 },
      tooltip: {
        trigger: 'axis',
        renderMode: 'richText',
        confine: true,
        formatter: (values: unknown) => {
          const first = Array.isArray(values) ? values[0] as { dataIndex?: number } : undefined
          const row = first?.dataIndex === undefined ? undefined : available[first.dataIndex]
          if (!row) return ''
          const value = accuracy
            ? `${((row.accuracy ?? 0) * 100).toFixed(1)}%`
            : (row.average_mastery ?? 0).toFixed(4)
          return `${row.knowledge_point_name}\n${accuracy ? '正确率' : '掌握度参考均值'}：${value}\n${row.evidence_student_count}/${row.student_count} 名学生有证据 · ${row.confirmed_attempt_count} 次确认作答`
        },
      },
      xAxis: accuracy
        ? { type: 'value', min: 0, max: 100, interval: 20, axisLabel: { formatter: '{value}%' } }
        : { type: 'value', min: 0, max: 1, interval: 0.2 },
      yAxis: {
        type: 'category', inverse: true,
        data: available.map((item) => item.knowledge_point_name),
        axisLabel: { width: 150, overflow: 'truncate', fontSize: 12 },
        axisTick: { show: false },
      },
      series: [{
        type: 'bar', barMaxWidth: 22,
        name: accuracy ? '正确率' : '掌握度参考均值',
        data: available.map((item) => {
          const value = accuracy ? (item.accuracy ?? 0) * 100 : item.average_mastery
          const lowEvidence = item.stable_student_count < item.evidence_student_count
          const warningThreshold = accuracy ? 40 : 0.4
          return {
            value,
            itemStyle: { color: lowEvidence ? '#8a94a3' : value !== null && value < warningThreshold ? '#c45c49' : '#168779' },
            label: {
              show: true,
              position: 'right',
              formatter: accuracy ? `${Number(value).toFixed(1)}%` : Number(value).toFixed(4),
            },
          }
        }),
      }],
    }
  }, [available, metric])

  if (!available.length) return <Empty description="当前班级暂无已确认的作答证据" />
  return <ProfileChart
    option={option}
    height={Math.max(280, available.length * 42 + 58)}
    label={metric === 'accuracy' ? '班级知识点正确率条形图' : '班级知识点掌握度参考均值条形图'}
    onPoint={(index) => { if (available[index]) onSelect(available[index].knowledge_point_code) }}
  />
}
