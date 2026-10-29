import { useEffect, useRef } from 'react'
import * as echarts from 'echarts/core'
import { BarChart, LineChart, RadarChart } from 'echarts/charts'
import { GridComponent, RadarComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { EChartsCoreOption } from 'echarts/core'

echarts.use([BarChart, LineChart, RadarChart, GridComponent, RadarComponent, TooltipComponent, CanvasRenderer])

export function ProfileChart({ option, height, label, onPoint }: {
  option: EChartsCoreOption; height: number; label: string; onPoint: (index: number) => void
}) {
  const container = useRef<HTMLDivElement>(null)
  const chart = useRef<echarts.EChartsType | null>(null)
  const callback = useRef(onPoint)
  callback.current = onPoint
  useEffect(() => {
    if (!container.current) return
    const instance = echarts.init(container.current)
    chart.current = instance
    instance.on('click', (event) => {
      if (event.componentType === 'series') callback.current(event.dataIndex)
    })
    const observer = new ResizeObserver(() => instance.resize())
    observer.observe(container.current)
    return () => { observer.disconnect(); instance.dispose(); chart.current = null }
  }, [])
  useEffect(() => { chart.current?.setOption(option, true) }, [option])
  return <div ref={container} className="profile-chart" style={{ height }} role="img" aria-label={label} />
}
