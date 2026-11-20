import type { SubmissionPaperItem } from '../types/submission'
import { answerUnitCoordinates } from './answerUnits'

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : null
}

export function equivalenceAdoption(item: SubmissionPaperItem) {
  const attempt = item.attempt
  const review = record(attempt?.grading_details.equivalence_review)
  if (!attempt || !review || !Number.isSafeInteger(review.applied_revision_no)) return null
  const revision = Number(review.applied_revision_no)
  if (revision < 1 || revision > attempt.revision_no) return null
  const decisions = record(review.decisions)
  if (!decisions) return null
  const reference = record(item.grading_answer_snapshot)
  const units = reference?.kind === 'group' && Array.isArray(reference.items)
    ? reference.items.map(record).filter((unit): unit is Record<string, unknown> => !!unit) : []
  const contextual = review.judgment_mode === 'contextual'
    || review.version === 'stage6-contextual-teacher-adoption-v2'
  const leaves = Object.entries(decisions).flatMap(([id, value]) => {
    const decision = record(value)
    if (!decision || typeof decision.task_id !== 'string'
      || !['equivalent', 'not_equivalent'].includes(String(decision.verdict))) return []
    const index = units.findIndex(unit => unit.item_id === id)
    const unit = units[index]
    const knownCoordinates = /^sub_[1-9]\d*\.blank_[1-9]\d*$/.test(id) || index >= 0
    const coordinates = knownCoordinates ? answerUnitCoordinates({
      item_id: id,
      subquestion_no: typeof unit?.subquestion_no === 'number' ? unit.subquestion_no : undefined,
      blank_no: typeof unit?.blank_no === 'number' ? unit.blank_no : undefined,
    }, index) : null
    return [{
      itemId: id,
      coordinates,
      label: coordinates ? `第 ${coordinates.subquestion_no} 小题 · 第 ${coordinates.blank_no} 空`
        : id === 'answer' ? '本题答案' : `空位 ${id}`,
      acceptable: decision.verdict === 'equivalent',
      verdict: contextual ? decision.verdict === 'equivalent' ? '本空可接受' : '本空不可接受'
        : decision.verdict === 'equivalent' ? '等价' : '不等价',
    }]
  }).sort((a, b) => (a.coordinates?.subquestion_no ?? 0) - (b.coordinates?.subquestion_no ?? 0)
    || (a.coordinates?.blank_no ?? 0) - (b.coordinates?.blank_no ?? 0)
    || a.itemId.localeCompare(b.itemId))
  if (!leaves.length) return null
  return {
    revision,
    current: revision === attempt.revision_no,
    reviewer: typeof review.reviewer === 'string' ? review.reviewer : '',
    reason: typeof review.reason === 'string' ? review.reason : '',
    leaves,
  }
}
