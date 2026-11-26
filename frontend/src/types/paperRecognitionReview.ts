export type PaperRecognitionReviewStatus = 'pending' | 'correct' | 'incorrect' | 'uncertain'

export interface PaperRecognitionReviewState {
  review_status: PaperRecognitionReviewStatus
  is_error: boolean | null
  all_pages_reviewed?: boolean
  reviewer?: string
  reason?: string
}

export interface PaperRecognitionReviewView {
  recognition_sha256: string | null
  revision_no: number
  transcription_revision_no: number | null
  target_scope: 'merged' | 'submitted'
  can_record: boolean
  stale: boolean
  message: string | null
  original_images_available: boolean
  synthetic: boolean | null
  page_count: number
  pages: Array<{ page_no: number; generation: number; provider: string; model: string }>
  current: PaperRecognitionReviewState
  history: Array<PaperRecognitionReviewState & {
    recognition_sha256: string
    revision_no: number
    is_current_target: boolean
    synthetic: boolean
    created_at: string
  }>
  unchanged?: boolean
}
