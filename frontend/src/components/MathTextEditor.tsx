import { CalculatorOutlined } from '@ant-design/icons'
import { Button, Input, Modal, Space, Typography, message } from 'antd'
import { useRef, useState } from 'react'
import { MathAnswerInput } from './MathAnswerInput'

interface MathTextEditorProps {
  value?: string
  onChange?: (value: string) => void
  rows?: number
  maxLength?: number
}

export function MathTextEditor({
  value = '',
  onChange,
  rows = 5,
  maxLength = 10000,
}: MathTextEditorProps) {
  const [formulaOpen, setFormulaOpen] = useState(false)
  const [formula, setFormula] = useState('')
  const selectionRef = useRef({ start: value.length, end: value.length })

  const rememberSelection = (target: HTMLTextAreaElement) => {
    selectionRef.current = {
      start: target.selectionStart ?? value.length,
      end: target.selectionEnd ?? value.length,
    }
  }

  const insertFormula = () => {
    const clean = formula.trim()
    if (!clean) {
      message.warning('请先输入公式')
      return
    }
    const { start, end } = selectionRef.current
    const token = `\\(${clean}\\)`
    const next = `${value.slice(0, start)}${token}${value.slice(end)}`
    if (next.length > maxLength) {
      message.error(`题干不能超过 ${maxLength} 个字符`)
      return
    }
    onChange?.(next)
    selectionRef.current = { start: start + token.length, end: start + token.length }
    setFormula('')
    setFormulaOpen(false)
  }

  return (
    <Space direction='vertical' size={8} className='full-width'>
      <Input.TextArea
        value={value}
        rows={rows}
        maxLength={maxLength}
        showCount
        onChange={(event) => onChange?.(event.target.value)}
        onSelect={(event) => rememberSelection(event.currentTarget)}
        onClick={(event) => rememberSelection(event.currentTarget)}
        onKeyUp={(event) => rememberSelection(event.currentTarget)}
      />
      <Space wrap>
        <Button icon={<CalculatorOutlined />} onClick={() => setFormulaOpen(true)}>在光标处插入公式</Button>
        <Typography.Text type='secondary'>公式将以可编辑的 LaTeX 形式保存，不再转成图片。</Typography.Text>
      </Space>
      <Modal
        title='插入题干公式'
        open={formulaOpen}
        okText='插入公式'
        cancelText='取消'
        onOk={insertFormula}
        onCancel={() => { setFormulaOpen(false); setFormula('') }}
        destroyOnClose
      >
        <MathAnswerInput
          value={formula}
          onChange={setFormula}
          outputFormat='latex'
          ariaLabel='题干公式'
          sourceLabel='将插入题干的 LaTeX'
        />
      </Modal>
    </Space>
  )
}
