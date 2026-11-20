import type { AssetInfo } from './index'

export interface AnswerCardArtifact {
  id: string
  paper_instance_id: string
  artifact_code: string
  revision_no: number
  layout_version: 'answer-card-v3'
  renderer_version: string
  source_sha256: string
  pdf_sha256: string
  manifest_sha256: string
  total_pages: number
  created_at: string
  pdf_url: string
  manifest_url: string
  reused: boolean
}

export type PrintSourceModule = 'required' | 'personalized' | 'history' | 'weekly_summary' | 'unclassified'

export interface PrintAnswerSlot {
  order_no: number
  blank_ids: string[]
  kind: string
  source_module: PrintSourceModule
  subquestions: Array<{
    subquestion_no: number
    blanks: Array<{
      item_id: string
      blank_no: number
      kind: 'choice' | 'fill_blank' | 'true_false' | 'manual'
    }>
  }>
}

export interface PrintView {
  paper_instance_id: string
  assignment_title: string
  student_code: string
  version_no: number
  instance_code: string
  layout_version: string
  is_active: boolean
  assignment_type: string
  items: Array<{
    id: string
    order_no: number
    question_snapshot: Record<string, unknown> & { question_text?: string; question_type?: string }
    options_snapshot: unknown
    assets: AssetInfo[]
  }>
  answer_slots: PrintAnswerSlot[]
}

export type RecognitionStatus = 'readable' | 'blank' | 'missing' | 'illegible' | 'ambiguous'

export interface RecognitionRun {
  id: string
  paper_instance_id: string
  student_id: string
  submission_id: string | null
  status: 'uploaded' | 'processing' | 'ready_for_review' | 'failed' | 'committed' | 'discarded'
  provider: string
  model: string
  prompt_version: string
  original_filename: string | null
  content_type: string
  byte_size: number
  pixel_width: number
  pixel_height: number
  error_code: string | null
  error_message: string | null
  started_at: string | null
  processing_stale: boolean
  image_purged_at: string | null
  image_url: string | null
  items: Array<{
    id: string
    paper_instance_item_id: string
    order_no: number
    recognition_status: RecognitionStatus
    recognized_answer: unknown
    confidence: number | null
  }>
}
