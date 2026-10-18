import type { AssetInfo } from './index'

export type GradeStatus = 'auto_confirmed' | 'needs_review' | 'teacher_confirmed'
export type GradingStatus = 'unsubmitted' | 'needs_review' | 'completed'

export interface AttemptResult {
  id: string
  paper_instance_item_id: string
  order_no: number
  student_answer: unknown
  normalized_answer: unknown
  grading_details: Record<string, unknown> & {
    auto_status?: string
    auto_is_correct?: boolean | null
    reason_code?: string
    item_results?: Array<Record<string, unknown>>
  }
  grade_status: GradeStatus
  is_correct: boolean | null
  manual_override: boolean
  revision_no: number
  answered_at: string
  reviewed_at: string | null
  used_in_profile: boolean
  recognition_status: string | null
  ocr_confidence: number | null
}

export interface SubmissionPaperItem {
  id: string
  question_id: string
  order_no: number
  question_snapshot: {
    original_qid?: string
    question_text?: string
    question_type?: string
    difficulty?: number
    assets?: Array<{ path: string }>
  }
  options_snapshot: unknown
  answer_snapshot: unknown
  answer_rule_snapshot: { answer_match_type?: string; answer_normalization_rule?: Record<string, unknown> }
  grading_answer_snapshot: unknown
  grading_answer_rule_snapshot: { answer_match_type?: string; answer_normalization_rule?: Record<string, unknown> }
  standard_answer_source: 'published_snapshot' | 'question_revision'
  answer_key_update_available: boolean
  knowledge_point_snapshot: { id: string; name: string }
  assets: AssetInfo[]
  attempt: AttemptResult | null
}

export interface SubmissionDetail {
  submission_id: string | null
  paper_instance_id: string
  assignment_id: string
  assignment_title: string
  instance_code: string | null
  version_no: number
  paper_status: string
  is_active: boolean
  can_submit: boolean
  student_id: string
  student_code: string
  student_name: string
  source: string | null
  recognition_run_id: string | null
  recognition_image_url: string | null
  recognition_image_purged_at: string | null
  recognition_provider: string | null
  recognition_model: string | null
  recognition_prompt_version: string | null
  submitted_at: string | null
  replayed: boolean
  unchanged: boolean
  answer_key_update_count: number
  regraded_count: number
  summary: {
    total_count: number
    correct_count: number
    incorrect_count: number
    needs_review_count: number
  }
  items: SubmissionPaperItem[]
  mastery_update_stats: Record<string, number> | null
}

export interface GradingQueueItem {
  paper_instance_id: string
  assignment_id: string
  assignment_title: string
  student_id: string
  student_code: string
  student_name: string
  version_no: number
  instance_code: string
  is_active: boolean
  invalidation_type: string | null
  grading_status: GradingStatus
  item_count: number
  needs_review_count: number
  submitted_at: string | null
}

export interface GradingQueue {
  items: GradingQueueItem[]
  total: number
}

export interface AttemptRevision {
  revision_no: number
  before_state: Record<string, unknown> | null
  after_state: Record<string, unknown>
  operator_type: string
  operator_id: string | null
  reason: string
  created_at: string
}

export interface AnswerDraft {
  response_status: 'unset' | 'answered' | 'blank'
  answer: unknown
}
