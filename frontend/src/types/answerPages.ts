export interface AnswerPageImage {
  id: string
  image_sha256: string
  width: number
  height: number
  byte_size: number
  created_at: string
  retention_until: string
  available: boolean
  image_url: string | null
}

export interface AnswerPageCollection {
  id: string
  artifact_id: string
  artifact_code: string
  revision_no: number
  total_pages: number
  status: 'collecting' | 'collected'
  missing_page_nos: number[]
  collection_ready: boolean
  is_active: boolean
  recognition_status: 'not_started'
  can_submit: false
  effective_input_sha256: string
  pages: Array<{ page_no: number; effective_image_id: string | null; status: 'uploaded' | 'expired' | 'missing' }>
  images: AnswerPageImage[]
  history: Array<{
    revision_no: number
    page_no: number
    action: string
    before_image_id: string | null
    after_image_id: string | null
    reviewer: string
    reason: string
    created_at: string
  }>
  reused?: boolean
  notice?: string
}
