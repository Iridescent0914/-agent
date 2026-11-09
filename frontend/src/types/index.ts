export interface Page<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}

export interface Tag {
  code: string
  name: string
}

export interface QuestionSummary {
  id: string
  original_qid: string
  question_type: 'choice' | 'fill_blank' | 'calculation' | 'solution' | 'true_false' | 'unknown'
  difficulty: number
  grade: string
  section: string
  question_text: string
  main_knowledge_point: Tag
  core_literacy: string
  is_required: boolean
  has_image: boolean
  has_equation: boolean
  asset_count: number
  grading_readiness: 'machine_ready' | 'raw_answer_only'
  import_status: string
  import_issue_count: number
}

export interface QuestionCatalogStats {
  total: number
  ready_count: number
  pending_count: number
  type_counts: Record<string, number>
}

export interface QuestionCatalogKnowledgePoint extends Tag {
  section: string | null
  mastery_level: string | null
  core_competencies: string[]
}

export interface QuestionCatalogChapter extends Tag {
  sections: string[]
  knowledge_points: QuestionCatalogKnowledgePoint[]
  grades?: string[]
  core_literacies?: string[]
}

export interface QuestionUpdateRequest {
  expected_review_revision: number
  original_qid?: string
  question_type: 'choice' | 'fill_blank' | 'calculation' | 'solution' | 'true_false'
  difficulty: number
  grade: string
  section: string
  question_text: string
  options?: string[]
  reference_answer_raw: string
  main_knowledge_point_code: string
  solution?: string
  wrong_analysis?: string
  estimated_score_rate?: string
  core_literacy: string
  is_required: boolean
  operator_name: string
  assets?: Array<{ path: string }>
}

export interface AssetInfo {
  path: string
  web_url: string | null
  conversion_status: string
}

export interface QuestionDetail extends QuestionSummary {
  chapter: Tag
  options: unknown
  correct_answer: unknown
  reference_answer_raw: string
  answer_match_type: string | null
  answer_normalization_rule: Record<string, unknown>
  solution: string | null
  wrong_analysis: string | null
  estimated_score_rate: string | null
  assets: AssetInfo[]
  source_file: string
  source_question_number: number
  source_review_status: string
  import_issues: string[]
  review_revision: number
  review_history: Array<Record<string, unknown>>
  reviewed_by: string | null
  reviewed_at: string | null
  created_at: string
  updated_at: string
}

export type QuestionAnswerMatchType = 'choice' | 'numeric' | 'exact' | 'latex_exact' | 'manual'

export interface QuestionAnswerItemInput {
  item_id: string
  subquestion_no?: number
  blank_no?: number
  answer: string
  accepted_answers: string[]
  answer_match_type: QuestionAnswerMatchType
}

export interface QuestionReviewRequest {
  final_answer_only?: boolean
  expected_review_revision: number
  reviewer_name: string
  review_note?: string
  items: QuestionAnswerItemInput[]
}

export interface QuestionWithdrawRequest {
  expected_review_revision: number
  reviewer_name: string
  review_note?: string
}

export interface StudentSummary {
  id: string
  student_code: string
  display_name: string
  class_id: string | null
  class_name: string
  is_demo: boolean
  attempt_count: number
  correct_count: number
  total_attempt_count: number
  pending_review_count: number
  latest_answered_at: string | null
}

export interface StudentCreateRequest {
  student_code: string
  display_name: string
  class_id: string
  class_name?: string
}

export interface ClassSummary {
  id: string
  name: string
  grade: string
  school_year: string
  teacher_name: string | null
  student_count: number
  created_at: string
  updated_at: string
}

export interface ClassWriteRequest {
  name: string
  grade: string
  school_year: string
  teacher_name?: string | null
}

export interface StudentDetail extends StudentSummary {
  created_at: string
  evidence_note: string
}

export interface StudentAttempt {
  id: string
  answered_at: string
  original_qid: string
  question_id: string
  question_text: string
  difficulty: number | null
  main_knowledge_point_code: string
  main_knowledge_point_name: string
  student_answer: unknown
  grade_status: string
  is_correct: boolean | null
  paper_instance_id: string | null
  paper_instance_item_id: string
  revision_no: number
  used_in_profile: boolean
  exclusion_reason: string | null
}
