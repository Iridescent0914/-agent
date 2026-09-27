import type { AssetInfo, Page } from './index'
import type { Recommendation, RecommendationParams, RecommendedQuestion } from './profile'

export interface AssignmentPreviewStudent {
  student_id: string
  student_code: string
  student_name: string
  recommendation: Recommendation
}

export interface AssignmentPreview {
  as_of: string
  student_count: number
  ready_count: number
  shortfall_student_count: number
  students: AssignmentPreviewStudent[]
}

export interface AssignmentRequest {
  student_ids: string[]
  recommendation_parameters: RecommendationParams
}

export interface PaperSummary {
  id: string
  assignment_id: string
  student_id: string
  student_code: string
  student_name: string
  version_no: number
  status: 'draft' | 'published'
  item_count: number
  requested_count: number | null
  shortfall: number | null
  edit_revision: number
  instance_code: string | null
  supersedes_instance_id: string | null
  created_at: string
  published_at: string | null
}

export interface AssignmentListItem {
  id: string
  title: string
  chapter_code: string
  assignment_type: string
  student_count: number
  draft_count: number
  published_count: number
  created_at: string
}

export interface AssignmentDetail extends AssignmentListItem {
  teaching_scope: Record<string, unknown>
  generation_config: Record<string, unknown> | null
  papers: PaperSummary[]
}

export interface PaperItem {
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
  answer_rule_snapshot: Record<string, unknown>
  exam_point_snapshot: { main?: { id: string; name: string }; related?: Array<{ id: string; name: string }> }
  assets: AssetInfo[]
  selection_audit: (RecommendedQuestion & { source?: string }) | null
}

export interface PaperInstance extends PaperSummary {
  recommendation_audit: (Recommendation & { schema_version?: string }) | null
  edit_history: Array<Record<string, unknown>>
  items: PaperItem[]
}

export interface Alternatives { items: RecommendedQuestion[] }
export type AssignmentPage = Page<AssignmentListItem>

