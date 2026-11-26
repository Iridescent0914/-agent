export interface AccuracySample {
  confirmed_attempt_count: number
  correct_count: number
  incorrect_count: number
  distinct_question_count: number
  pending_review_count: number
  excluded_attempt_count: number
  accuracy: number | null
}

export interface PaperAccuracyComparison {
  paper_instance_id: string
  computed_at: string
  counting_unit: 'attempt_question_group'
  history_scope: 'all_history_excluding_current_paper'
  read_only: true
  data_status: 'inactive_paper' | 'no_paper_results' | 'no_history' | 'incomplete_paper' | 'available'
  paper: AccuracySample & {
    total_question_count: number
    unsubmitted_question_count: number
    judged_coverage: number | null
  }
  history: AccuracySample
  knowledge_coverage: {
    seen_question_count: number
    unseen_question_count: number
    unmapped_question_count: number
    points: Array<{
      knowledge_point_code: string | null
      knowledge_point_name: string
      paper_question_count: number
      historical_confirmed_attempt_count: number
      history_status: 'seen' | 'unseen' | 'unmapped'
    }>
  }
}
