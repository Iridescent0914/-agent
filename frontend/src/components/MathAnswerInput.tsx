import { useEffect, useRef, useState } from 'react'
import type { MathfieldElement } from 'mathlive'
import 'mathlive/fonts.css'
import { hasIncompleteMath } from '../utils/answerUnits'

interface MathAnswerInputProps {
  value: string
  onChange: (value: string) => void
  outputFormat?: 'latex' | 'ascii-math'
  disabled?: boolean
  ariaLabel?: string
  sourceLabel?: string
}

interface MathTemplate {
  label: string
  ariaLabel: string
  latex: string
}

const COMMON_MATH_TEMPLATES: MathTemplate[] = [
  { label: 'a⁄b', ariaLabel: '插入分数', latex: '\\frac{#@}{#?}' },
  { label: '√x', ariaLabel: '插入平方根', latex: '\\sqrt{#0}' },
  { label: 'ⁿ√x', ariaLabel: '插入n次根', latex: '\\sqrt[#?]{#0}' },
  { label: 'x²', ariaLabel: '插入平方', latex: '#@^{2}' },
  { label: 'xⁿ', ariaLabel: '插入幂', latex: '#@^{#?}' },
  { label: 'xₙ', ariaLabel: '插入下标', latex: '#@_{#?}' },
  { label: '|x|', ariaLabel: '插入绝对值', latex: '\\left|#0\\right|' },
  { label: '(x)', ariaLabel: '插入括号', latex: '\\left(#0\\right)' },
  { label: 'π', ariaLabel: '插入圆周率', latex: '\\pi' },
  { label: '±', ariaLabel: '插入正负号', latex: '\\pm' },
  { label: '×', ariaLabel: '插入乘号', latex: '\\times' },
  { label: '÷', ariaLabel: '插入除号', latex: '\\div' },
  { label: '≤', ariaLabel: '插入小于等于号', latex: '\\le' },
  { label: '≥', ariaLabel: '插入大于等于号', latex: '\\ge' },
  { label: '≠', ariaLabel: '插入不等号', latex: '\\ne' },
  { label: '°', ariaLabel: '插入度数符号', latex: '^{\\circ}' },
]

function exportedValue(field: MathfieldElement, format: 'latex' | 'ascii-math') {
  const latex = field.getValue('latex')
  // ASCII serialization can discard empty template slots. Never turn an
  // unfinished fraction into an apparently complete integer for grading.
  return hasIncompleteMath(latex) ? latex : field.getValue(format)
}

function restoreValue(field: MathfieldElement, value: string, format: 'latex' | 'ascii-math', asciiToLatex: (text: string) => string) {
  // setValue() always reads LaTeX even though its option type exposes format.
  const latex = format === 'ascii-math' && !value.includes('\\') ? asciiToLatex(value) : value
  field.setValue(latex, { silenceNotifications: true })
}

export function MathAnswerInput({
  value,
  onChange,
  outputFormat = 'latex',
  disabled = false,
  ariaLabel = '公式答案',
  sourceLabel,
}: MathAnswerInputProps) {
  const hostRef = useRef<HTMLDivElement>(null)
  const mathfieldRef = useRef<MathfieldElement | null>(null)
  const onChangeRef = useRef(onChange)
  const lastEmittedRef = useRef<string | null>(null)
  const asciiToLatexRef = useRef<(text: string) => string>(text => text)
  const latestRef = useRef({ value, disabled, outputFormat, ariaLabel })
  latestRef.current = { value, disabled, outputFormat, ariaLabel }
  const [incomplete, setIncomplete] = useState(hasIncompleteMath(value))

  useEffect(() => {
    onChangeRef.current = onChange
  }, [onChange])

  useEffect(() => {
    const host = hostRef.current
    if (!host) return

    let cancelled = false
    let mathfield: MathfieldElement | null = null
    let handleInput: (() => void) | null = null
    void import('mathlive').then(({ MathfieldElement, convertAsciiMathToLatex }) => {
      if (cancelled) return
      window.mathVirtualKeyboard.layouts = ['numeric', 'symbols', 'alphabetic', 'greek']
      asciiToLatexRef.current = convertAsciiMathToLatex
      mathfield = new MathfieldElement()
      restoreValue(mathfield, latestRef.current.value, latestRef.current.outputFormat, asciiToLatexRef.current)
      mathfield.mathVirtualKeyboardPolicy = 'auto'
      mathfield.smartFence = true
      mathfield.readOnly = latestRef.current.disabled
      mathfield.placeholder = '\\text{输入数学公式}'
      mathfield.setAttribute('aria-label', latestRef.current.ariaLabel)
      handleInput = () => {
        if (!mathfield) return
        const nextValue = exportedValue(mathfield, latestRef.current.outputFormat)
        setIncomplete(hasIncompleteMath(mathfield.getValue('latex')))
        lastEmittedRef.current = nextValue
        onChangeRef.current(nextValue)
      }
      mathfield.addEventListener('input', handleInput)
      host.replaceChildren(mathfield)
      mathfieldRef.current = mathfield
    })

    return () => {
      cancelled = true
      if (mathfield && handleInput) mathfield.removeEventListener('input', handleInput)
      mathfield?.remove()
      mathfieldRef.current = null
    }
  }, [])

  useEffect(() => {
    const mathfield = mathfieldRef.current
    if (value === lastEmittedRef.current) {
      return
    }
    lastEmittedRef.current = null
    if (mathfield && exportedValue(mathfield, outputFormat) !== value) {
      restoreValue(mathfield, value, outputFormat, asciiToLatexRef.current)
    }
    setIncomplete(hasIncompleteMath(value))
  }, [value, outputFormat])

  useEffect(() => {
    if (mathfieldRef.current) mathfieldRef.current.readOnly = disabled
  }, [disabled])

  const resolvedSourceLabel = sourceLabel ?? (outputFormat === 'ascii-math' ? '提交的算式' : '保存的 LaTeX')
  const openKeyboard = () => {
    const mathfield = mathfieldRef.current
    if (!mathfield || disabled) return
    mathfield.focus()
    mathfield.executeCommand('showVirtualKeyboard')
  }

  const insertTemplate = (template: MathTemplate) => {
    const mathfield = mathfieldRef.current
    if (!mathfield || disabled) return
    mathfield.insert(template.latex, {
      format: 'latex',
      insertionMode: 'replaceSelection',
      selectionMode: 'placeholder',
      focus: true,
      feedback: true,
    })
  }

  return (
    <div>
      <div ref={hostRef} className="math-answer-input" />
      <div className="math-template-toolbar" role="toolbar" aria-label="常用公式">
        {COMMON_MATH_TEMPLATES.map((template) => <button
          key={template.ariaLabel}
          type="button"
          className="math-template-button"
          disabled={disabled}
          aria-label={template.ariaLabel}
          title={template.ariaLabel}
          onClick={() => insertTemplate(template)}
        >
          {template.label}
        </button>)}
      </div>
      <button type="button" className="math-keyboard-button" disabled={disabled} onClick={openKeyboard}>
        打开完整公式键盘
      </button>
      <div className="latex-source">{resolvedSourceLabel}：{value || '（尚未输入）'}</div>
      <div className="math-input-hint">分数请先填写分子，再按 Tab 切换到分母；也可直接点击分母空位。</div>
      {incomplete && <div role="alert" className="math-input-warning">公式尚未填写完整，请补充分子、分母或其他公式空位后再提交。</div>}
    </div>
  )
}
