import { Empty, Typography } from 'antd'
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
  const unassessedCount = useMemo(
    () => rows.filter((row) => row.accuracy === null).length,
    [rows],
  )
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
      data: rows.map((row) => row.section ? `${row.section} · ${row.name}` : row.name),
      axisLabel: { width: 150, overflow: 'truncate', fontSize: 12 },
      axisTick: { show: false },
    },
    legend: { bottom: 0, data: showClassAverage ? ['学生', '班级平均'] : ['学生'] },
    series: [
      {
        type: 'bar', name: '学生', barMaxWidth: 18,
        itemStyle: { color: '#176c61' },
        data: rows.map((row) => ({
          value: Number(((row.accuracy ?? 0) * 100).toFixed(1)),
          itemStyle: { color: row.accuracy === null ? '#c8d0ce' : '#176c61' },
        })),
      },
      ...(showClassAverage ? [{
        type: 'bar', name: '班级平均', barMaxWidth: 18,
        itemStyle: { color: '#315b8a' },
        data: rows.map((row) => ({
          value: Number(((row.classAverage ?? 0) * 100).toFixed(1)),
          itemStyle: { color: row.classAverage === null ? '#d7dce3' : '#315b8a' },
        })),
      }] : []),
    ],
  }), [rows, showClassAverage])

  if (!rows.length) return <Empty description="当前章节暂无知识点" />
  return <div>
    <ProfileChart option={option} height={Math.max(280, rows.length * 42 + 72)} label={label} onPoint={() => undefined} />
    {unassessedCount > 0 && <Typography.Text type="secondary">
      本章共 {rows.length} 个知识点，其中 {unassessedCount} 个暂无有效确认作答，图中按 0% 展示且不计入正确率分母。
    </Typography.Text>}
  </div>
}
