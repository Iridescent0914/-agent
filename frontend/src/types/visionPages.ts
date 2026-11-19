export type PageVisionStatus = 'queued' | 'running' | 'needs_review' | 'failed' | 'stale' | 'cancelled'

export interface PageVisionTask {
  id: string
  page_no: number
  image_id: string
  generation: number
  revision_no: number
  status: PageVisionStatus
  effective_status: PageVisionStatus
  is_current: boolean
  synthetic: boolean
  provider: string
  model: string
  attempt_count: number
  max_attempts: number
  error_code: string | null
  binding_error: string | null
  received_unit_count: number
  can_submit: false
  identity_check: { status: 'verified' | 'unconfirmed' | 'mismatch'; mismatched_fields: string[]; unknown_fields: string[] } | null
}

export interface PageVisionTasks {
  configuration: { provider: string; model?: string; synthetic?: boolean; error_code: string | null; real_provider_available: boolean }
  budget?: { scope: string; calls: number; unknown_calls: number; estimated_cost_cny: number; reserved_cost_cny: number; max_calls: number; max_budget_cny: number }
  collection_id: string
  collection_revision: number
  current_tasks: PageVisionTask[]
  history: PageVisionTask[]
  can_submit: false
  created_count?: number
}

export interface PageVisionTaskDetail extends PageVisionTask {
  snapshot: { slots: Array<{ slot_token: string; order_no: number; subquestion_no: number; blank_no: number }> }
  result: {
    answers: Array<{ slot_token: string; recognition_status: string; answer: string | null; confidence: number | null }>
  } | null
  calls: Array<{ id: string; call_no: number; status: string; raw_response: string | null; error_code: string | null; usage: { cost_cny: number | null; usage_status?: string; synthetic?: boolean }; late_response: boolean }>
  action_history: Array<{ action: string; reviewer: string; reason: string; at: string }>
}
