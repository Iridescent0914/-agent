export interface ClassOption {
  class_name: string
  student_count: number
}

export interface ClassKnowledgePointAnalytics {
  knowledge_point_id: string
  knowledge_point_code: string
  knowledge_point_name: string
  student_count: number
  evidence_student_count: number
  stable_student_count: number
  unassessed_student_count: number
  confirmed_attempt_count: number
  correct_attempt_count: number
  incorrect_attempt_count: number
  accuracy: number | null
  average_mastery: number | null
  recent_wrong_count: number
  available_question_count: number
}

export interface ClassFocusPoint {
  knowledge_point_code: string
  knowledge_point_name: string
  category: 'practice' | 'recent_errors' | 'observe' | 'diagnostic'
  reason: string
}

export interface ClassOverview {
  class_name: string
  chapter_code: string
  computed_at: string
  rule_version: string
  summary: {
    student_count: number
    evidence_student_count: number
    confirmed_attempt_count: number
    pending_review_count: number
    invalid_evidence_count: number
    covered_knowledge_point_count: number
    uncovered_knowledge_point_count: number
  }
  knowledge_points: ClassKnowledgePointAnalytics[]
  focus_points: ClassFocusPoint[]
  evidence_note: string
}

export interface KnowledgePointStudentAnalytics {
  student_id: string
  student_code: string
  student_name: string
  assessment_status: 'unassessed' | 'insufficient' | 'assessed'
  mastery: number | null
  attempt_count: number
  correct_count: number
  accuracy: number | null
  recent_wrong_count: number
  last_practiced_at: string | null
}

export interface KnowledgePointStudentList {
  class_name: string
  chapter_code: string
  knowledge_point_code: string
  knowledge_point_name: string
  computed_at: string
  items: KnowledgePointStudentAnalytics[]
}

export type AssignmentGradingStatus = 'unsubmitted' | 'needs_review' | 'completed'

export interface AssignmentProgressStudent {
  paper_instance_id: string
  student_id: string
  student_code: string
  student_name: string
  version_no: number
  instance_code: string
  item_count: number
  grading_status: AssignmentGradingStatus
  needs_review_count: number
  submitted_at: string | null
}

export interface AssignmentAnalytics {
  assignment_id: string
  assignment_title: string
  chapter_code: string
  computed_at: string
  summary: {
    student_count: number
    draft_count: number
    active_published_count: number
    historical_invalidated_count: number
    submitted_count: number
    unsubmitted_count: number
    needs_review_count: number
    completed_count: number
    pending_attempt_count: number
    submission_rate: number | null
    completion_rate: number | null
  }
  students: AssignmentProgressStudent[]
}
