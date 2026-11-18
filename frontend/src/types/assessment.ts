export type EquivalenceVerdict = 'equivalent' | 'not_equivalent' | 'uncertain'
export type AssessmentStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'stale'

export interface Stage6Status {
  enabled: boolean
  provider: string
  model?: string | null
  real_provider_available: boolean
  config_error?: string | null
  budget?: { calls: number; max_calls: number; estimated_cost_cny: number; reserved_cost_cny: number; max_budget_cny: number; unknown_calls: number } | null
}

export interface EquivalencePreview {
  match_type?: string
  eligible: boolean
  reason_code: string
  message: string
  item_id: string
  input_revision: number
  attempt_revision_no: number | null
  reference_answer: string
  student_answer: unknown
  reference_source: string
  rule_result: { reason_code: string; is_correct: boolean | null } | null
}

export interface AssessmentTask {
  id: string
  status: AssessmentStatus
  input_revision: number
  attempt_count: number
  max_attempts: number
  retryable: boolean
  synthetic: boolean
  provider: string
  model: string
  error_code: string | null
  created_at: string
  snapshot: { item_id: string; subquestion_no: number; blank_no: number; student_answer: string }
  request_payload: { reference_answer: string; student_answer: string }
  result: { verdict: EquivalenceVerdict; explanation: string } | null
}

export interface QualityReview {
  review_status: 'unreviewed' | 'correct' | 'incorrect' | 'uncertain'
  is_error: boolean | null
  error_type?: string | null
  gold?: { verdict: EquivalenceVerdict } | null
  reviewer?: string
  reason?: string
  reviewed_at?: string
}

export interface QualityFeedback {
  revision_no: number
  input_stale: boolean
  current: QualityReview
  history: Array<QualityReview & { revision_no: number }>
}
