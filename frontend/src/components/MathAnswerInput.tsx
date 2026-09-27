import { useEffect, useRef } from 'react'
import type { MathfieldElement } from 'mathlive'
import 'mathlive/fonts.css'

interface MathAnswerInputProps {
  value: string
  onChange: (value: string) => void
}

export function MathAnswerInput({ value, onChange }: MathAnswerInputProps) {
  const hostRef = useRef<HTMLDivElement>(null)
  const mathfieldRef = useRef<MathfieldElement | null>(null)
  const onChangeRef = useRef(onChange)

  useEffect(() => {
    onChangeRef.current = onChange
  }, [onChange])

  useEffect(() => {
    const host = hostRef.current
    if (!host) return

    let cancelled = false
    let mathfield: MathfieldElement | null = null
    let handleInput: (() => void) | null = null
    void import('mathlive').then(({ MathfieldElement }) => {
      if (cancelled) return
      mathfield = new MathfieldElement()
      mathfield.value = value
      mathfield.mathVirtualKeyboardPolicy = 'manual'
      mathfield.smartFence = true
      mathfield.placeholder = '\\text{输入数学公式}'
      mathfield.setAttribute('aria-label', '公式答案')
      handleInput = () => onChangeRef.current(mathfield?.value || '')
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
    if (mathfield && mathfield.value !== value) {
      mathfield.value = value
    }
  }, [value])

  return (
    <div>
      <div ref={hostRef} className="math-answer-input" />
      <div className="latex-source">保存的 LaTeX：{value || '（尚未输入）'}</div>
    </div>
  )
}
