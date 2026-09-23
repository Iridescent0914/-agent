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
  question_type: 'choice' | 'fill_blank'
  difficulty: number
  section: string
  question_text: string
  main_knowledge_point: Tag
  main_exam_point: Tag
  has_image: boolean
  has_equation: boolean
  asset_count: number
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
  answer_match_type: string
  answer_normalization_rule: Record<string, unknown>
  related_exam_points: Tag[]
  solution: string | null
  wrong_analysis: string | null
  estimated_score_rate: string | null
  assets: AssetInfo[]
  source_file: string
  source_question_number: number
  source_review_status: string
  grading_readiness: string
  created_at: string
  updated_at: string
}

export interface StudentSummary {
  id: string
  student_code: string
  display_name: string
  class_name: string
  is_demo: boolean
  attempt_count: number
  correct_count: number
  latest_answered_at: string | null
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
  difficulty: number
  main_exam_point_code: string
  main_exam_point_name: string
  student_answer: unknown
  grade_status: string
  is_correct: boolean | null
  paper_instance_id: string
  paper_instance_item_id: string
}
