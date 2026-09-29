import { Checkbox, Input, Radio, Segmented, Space, Typography } from 'antd'
import { useState } from 'react'
import type { AnswerDraft, SubmissionPaperItem } from '../types/submission'
import { MathAnswerInput } from './MathAnswerInput'

interface GroupAnswerItem { item_id: string; answer: string | null; answer_match_type?: string }
interface GroupAnswer { kind: 'group'; items: GroupAnswerItem[] }

function isGroup(value: unknown): value is GroupAnswer {
  return Boolean(value && typeof value === 'object' && (value as GroupAnswer).kind === 'group' && Array.isArray((value as GroupAnswer).items))
}

function AnswerField({ matchType, value, disabled, onChange }: {
  matchType: string | undefined
  value: string
  disabled: boolean
  onChange: (value: string) => void
}) {
  const preferredFormula = matchType === 'numeric' || matchType === 'latex_exact'
  const [inputMode, setInputMode] = useState<'text' | 'formula'>(preferredFormula ? 'formula' : 'text')
  const numeric = matchType === 'numeric'
  return <Space direction="vertical" size="small" className="manual-answer-field-stack">
    <Space size="small">
      <Typography.Text type="secondary">输入方式</Typography.Text>
      <Segmented
        size="small"
        value={inputMode}
        disabled={disabled}
        options={[{ value: 'text', label: '普通输入' }, { value: 'formula', label: '公式键盘' }]}
        onChange={(mode) => setInputMode(mode as 'text' | 'formula')}
      />
    </Space>
    {inputMode === 'formula' ? <MathAnswerInput
      value={value}
      disabled={disabled}
      outputFormat={numeric ? 'ascii-math' : 'latex'}
      ariaLabel={numeric ? '数值公式答案' : 'LaTeX 公式答案'}
      sourceLabel={numeric ? '提交的算式' : '提交的 LaTeX'}
      onChange={onChange}
    /> : <Input
      value={value}
      disabled={disabled}
      placeholder="留空则表示该小题未作答"
      onChange={(event) => onChange(event.target.value)}
    />}
  </Space>
}

export function initialDraft(item: SubmissionPaperItem): AnswerDraft {
  if (isGroup(item.answer_snapshot)) {
    return {
      response_status: 'unset',
      answer: { kind: 'group', items: item.answer_snapshot.items.map((part) => ({ item_id: part.item_id, answer: null })) },
    }
  }
  return { response_status: 'unset', answer: '' }
}

export function ManualAnswerEditor({ item, value, onChange, disabled = false }: {
  item: SubmissionPaperItem
  value: AnswerDraft
  onChange: (value: AnswerDraft) => void
  disabled?: boolean
}) {
  const matchType = item.answer_rule_snapshot.answer_match_type
  const options = item.options_snapshot && typeof item.options_snapshot === 'object'
    ? Object.entries(item.options_snapshot as Record<string, unknown>) : []
  const group = isGroup(item.answer_snapshot)
  const groupValue = isGroup(value.answer) ? value.answer : null
  const expectedTypeById = new Map(
    group && isGroup(item.answer_snapshot)
      ? item.answer_snapshot.items.map((part) => [part.item_id, part.answer_match_type])
      : [],
  )

  const setBlank = (checked: boolean) => {
    if (checked) onChange({ response_status: 'blank', answer: null })
    else onChange(initialDraft(item))
  }
  const setGroupPart = (itemId: string, answer: string) => {
    if (!groupValue) return
    onChange({
      response_status: 'answered',
      answer: {
        kind: 'group',
        items: groupValue.items.map((part) => part.item_id === itemId ? { ...part, answer: answer || null } : part),
      },
    })
  }

  return <Space direction="vertical" className="answer-editor" size="middle">
    <Checkbox checked={value.response_status === 'blank'} disabled={disabled} onChange={(event) => setBlank(event.target.checked)}>学生整题留空</Checkbox>
    {value.response_status !== 'blank' && group && groupValue && <Space direction="vertical" className="group-answer-editor">
      {groupValue.items.map((part) => <Space key={part.item_id} align="start" className="group-answer-row">
        <Typography.Text strong>小题 {part.item_id}</Typography.Text>
        <div className="manual-answer-field">
          <AnswerField
            matchType={expectedTypeById.get(part.item_id)}
            value={part.answer ?? ''}
            disabled={disabled}
            onChange={(answer) => setGroupPart(part.item_id, answer)}
          />
        </div>
      </Space>)}
    </Space>}
    {value.response_status !== 'blank' && !group && matchType === 'choice' && <Radio.Group
      value={value.response_status === 'answered' ? value.answer : undefined}
      disabled={disabled}
      onChange={(event) => onChange({ response_status: 'answered', answer: event.target.value })}
    >
      <Space wrap>{options.map(([key, option]) => <Radio.Button key={key} value={key}>{key} · {String(option)}</Radio.Button>)}</Space>
    </Radio.Group>}
    {value.response_status !== 'blank' && !group && matchType !== 'choice' && <div className="manual-answer-field">
      <AnswerField
        matchType={matchType}
        value={typeof value.answer === 'string' ? value.answer : ''}
        disabled={disabled}
        onChange={(answer) => onChange({ response_status: 'answered', answer })}
      />
    </div>}
  </Space>
}
