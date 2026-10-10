import { useEffect, useRef } from 'react'
import type { MathfieldElement } from 'mathlive'
import 'mathlive/fonts.css'

export function ReadOnlyMath({ latex, block = false }: { latex: string; block?: boolean }) {
  const hostRef = useRef<HTMLSpanElement>(null)

  useEffect(() => {
    const host = hostRef.current
    if (!host) return

    let cancelled = false
    let mathfield: MathfieldElement | null = null
    void import('mathlive').then(({ MathfieldElement }) => {
      if (cancelled) return
      mathfield = new MathfieldElement()
      mathfield.value = latex
      mathfield.readOnly = true
      mathfield.mathVirtualKeyboardPolicy = 'manual'
      mathfield.setAttribute('tabindex', '-1')
      mathfield.setAttribute('aria-label', `数学公式：${latex}`)
      host.replaceChildren(mathfield)
    })

    return () => {
      cancelled = true
      mathfield?.remove()
    }
  }, [latex])

  return <span ref={hostRef} className={block ? 'question-formula question-formula-block' : 'question-formula'} />
}
