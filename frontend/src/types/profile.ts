import type { AssetInfo } from './index'

export interface EvidenceRef { attempt_id: string; revision_no: number }
export interface KnowledgePoint {
  id: string
  code: string
  name: string
  chapter_code: string
  chapter_name: string
  section?: string | null
  available_question_count: number
  required_available_question_count: number
}
export interface Mastery {
  knowledge_point_id: string
  knowledge_point_code: string
  knowledge_point_name: string
  assessment_status: 'unassessed' | 'insufficient' | 'assessed'
  mastery: number | null
  confidence_level: 'low' | 'medium' | 'high'
  attempt_count: number
  correct_count: number
  distinct_question_count: number
  recent_attempt_count: number
  recent_wrong_count: number
  last_practiced_at: string | null
  first_practiced_at: string | null
  practiced_question_types: string[]
  practiced_difficulties: number[]
  available_question_count: number
}
export interface LiteracyScore {
  name: string
  score: number
  score_source: 'baseline' | 'computed'
  rule_version: string
}
export interface HistoryPoint extends EvidenceRef {
  answered_at: string
  question_id: string
  paper_instance_item_id: string
  is_correct: boolean
  mastery_before: number
  mastery_after: number
}
export interface MasteryHistory { mastery: Mastery; points: HistoryPoint[]; rule_version: string }
export interface FocusPoint {
  knowledge_point_code: string
  knowledge_point_name: string
  category: 'practice' | 'recent_errors' | 'observe' | 'diagnostic'
  reason: string
  evidence: EvidenceRef[]
}
export interface Profile {
  student_id: string
  computed_at: string
  rule_version: string
  summary: {
    total_attempt_count: number; valid_attempt_count: number; pending_review_count: number
    invalid_evidence_count: number; distinct_question_count: number
    assessed_knowledge_point_count: number; unassessed_knowledge_point_count: number
  }
  masteries: Mastery[]
  literacies?: LiteracyScore[]
  focus_points: FocusPoint[]
  recent_errors: Array<EvidenceRef & { answered_at: string; original_qid: string; knowledge_point_code: string; knowledge_point_name: string }>
  excluded_evidence: Array<EvidenceRef & { reason: string }>
  evidence_note: string
}
export interface AttemptEvidence {
  id: string; revision_no: number; answered_at: string; reviewed_at: string | null
  question_id: string; paper_instance_item_id: string; paper_instance_id: string | null; instance_code: string | null
  question_snapshot: { original_qid?: string; question_text?: string; question_type?: string; difficulty?: number }
  options_snapshot: unknown; answer_snapshot: unknown; answer_rule_snapshot: Record<string, unknown>
  knowledge_point_snapshot: { id: string; name: string }
  assets: AssetInfo[]; student_answer: unknown; grade_status: string; is_correct: boolean | null
  used_in_profile: boolean; exclusion_reason: string | null
  revision: { revision_no: number; before_state: unknown; after_state: unknown; reason: string; operator_type: string; created_at: string } | null
}
export interface RecommendationParams {
  chapter_code: string; new_knowledge_point_codes: string[]; review_knowledge_point_codes: string[]
  chapter_codes?: string[]; grade_codes?: string[]
  new_count: number; review_count: number; question_types: string[]
  base_difficulty: number; min_difficulty: number; max_difficulty: number
  avoid_recent_days: number; max_knowledge_point_ratio: number
}
export interface ScoreParts { weakness: number; recent_wrong: number; difficulty_match: number; forgetting: number }
export interface RecommendedQuestion {
  question_id: string; original_qid: string; question_type: string; difficulty: number
  knowledge_point_code: string; knowledge_point_name: string; bucket: 'new' | 'review'
  strategy: 'diagnostic' | 'personalized'; score: number; target_difficulty: number
  components: ScoreParts; contributions: ScoreParts; reason_codes: string[]; reason_text: string; evidence: EvidenceRef[]
  source_module?: 'required' | 'personalized' | 'history' | 'legacy_recommendation'
}
export interface Recommendation {
  student_id: string; as_of: string; rule_version: string; parameters: RecommendationParams; weights: ScoreParts
  status: 'ready' | 'insufficient_candidates'; requested_count: number; selected_count: number; shortfall: number
  new_shortfall: number; review_shortfall: number; max_per_knowledge_point: number; selected_per_knowledge_point: Record<string, number>
  diagnostics: Array<{ bucket: 'new' | 'review'; knowledge_point_codes: string[]; requested_count: number; selected_count: number
    shortfall: number; stages: Array<{ code: string; remaining: number; removed: number }>
    reason_codes: string[]; capped_knowledge_point_codes: string[] }>
  items: RecommendedQuestion[]
}
