const answerMatchTypeLabels: Record<string, string> = {
  choice: '选项匹配',
  numeric: '数值匹配',
  exact: '精确文本',
  latex_exact: '公式匹配',
  group: '多答案项',
  manual: '教师人工评分',
}

export function answerMatchTypeLabel(value: string | null | undefined): string {
  if (!value) return '待确认'
  return answerMatchTypeLabels[value] || value
}
