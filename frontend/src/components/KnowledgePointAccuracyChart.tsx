import { Empty } from 'antd'
import { useMemo } from 'react'
import { ProfileChart } from './ProfileChart'

export interface AccuracyRow {
  code: string
  name: string
  section?: string | null
  accuracy: number | null
  classAverage: number | null
}

export function KnowledgePointAccuracyChart({
  rows,
  showClassAverage = true,
  label = '知识点正确率柱状图',
}: {
  rows: AccuracyRow[]
  showClassAverage?: boolean
  label?: string
}) {
  const visible = useMemo(() => rows.filter((row) => row.accuracy !== null || row.classAverage !== null), [rows])
  const option = useMemo(() => ({
    animation: false,
    grid: { left: 170, right: 70, top: 18, bottom: 34 },
    tooltip: {
      trigger: 'axis',
      renderMode: 'richText',
      confine: true,
      valueFormatter: (value: unknown) => `${Number(value).toFixed(1)}%`,
    },
    xAxis: { type: 'value', min: 0, max: 100, interval: 20, axisLabel: { formatter: '{value}%' } },
    yAxis: {
      type: 'category', inverse: true,
      data: visible.map((row) => row.section ? `${row.section} · ${row.name}` : row.name),
      axisLabel: { width: 150, overflow: 'truncate', fontSize: 12 },
      axisTick: { show: false },
    },
    legend: { bottom: 0, data: showClassAverage ? ['学生', '班级平均'] : ['学生'] },
    series: [
      {
        type: 'bar', name: '学生', barMaxWidth: 18,
        data: visible.map((row) => row.accuracy === null ? '-' : Number((row.accuracy * 100).toFixed(1))),
        itemStyle: { color: '#176c61' },
      },
      ...(showClassAverage ? [{
        type: 'bar', name: '班级平均', barMaxWidth: 18,
        data: visible.map((row) => row.classAverage === null ? '-' : Number((row.classAverage * 100).toFixed(1))),
        itemStyle: { color: '#315b8a' },
      }] : []),
    ],
  }), [showClassAverage, visible])

  if (!visible.length) return <Empty description="当前章节暂无已确认的作答证据" />
  return <ProfileChart option={option} height={Math.max(280, visible.length * 42 + 72)} label={label} onPoint={() => undefined} />
}
