import type { AssetInfo } from './index'

export interface PrintView {
  paper_instance_id: string
  assignment_title: string
  student_code: string
  version_no: number
  instance_code: string
  layout_version: string
  is_active: boolean
  items: Array<{
    id: string
    order_no: number
    question_snapshot: Record<string, unknown> & { question_text?: string; question_type?: string }
    options_snapshot: unknown
    assets: AssetInfo[]
  }>
  answer_slots: Array<{ order_no: number; blank_ids: string[]; kind: string }>
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
  image_url: string
  items: Array<{
    id: string
    paper_instance_item_id: string
    order_no: number
    recognition_status: RecognitionStatus
    recognized_answer: unknown
    confidence: number | null
  }>
}
