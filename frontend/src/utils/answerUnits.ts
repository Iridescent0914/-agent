interface UnitCoordinates {
  item_id: string
  subquestion_no?: number
  blank_no?: number
}

const positive = (value: number | undefined) => Number.isSafeInteger(value) && Number(value) > 0 ? value : undefined

export function answerUnitCoordinates(unit: UnitCoordinates, index: number, reference?: UnitCoordinates) {
  const canonical = /^sub_([1-9]\d*)\.blank_([1-9]\d*)$/.exec(unit.item_id)
  if (canonical) return { subquestion_no: Number(canonical[1]), blank_no: Number(canonical[2]) }
  const numericId = /^[1-9]\d*$/.test(unit.item_id) ? Number(unit.item_id) : undefined
  return {
    subquestion_no: positive(unit.subquestion_no) ?? positive(reference?.subquestion_no) ?? positive(numericId) ?? index + 1,
    blank_no: positive(unit.blank_no) ?? positive(reference?.blank_no) ?? 1,
  }
}

export function hasIncompleteMath(answer: unknown): boolean {
  if (typeof answer === 'string') return /\\placeholder\b|\\(?:dfrac|tfrac|frac)\s*(?:\{\s*\}|\{[^{}]*\}\s*\{\s*\})/.test(answer)
  if (!answer || typeof answer !== 'object') return false
  const group = answer as { kind?: string; items?: Array<{ answer?: unknown }> }
  return group.kind === 'group' && Array.isArray(group.items) && group.items.some(part => hasIncompleteMath(part.answer))
}
