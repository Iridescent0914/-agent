import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react'
import { flushSync } from 'react-dom'
import { apiGet, apiPost, apiUrl } from '../api/client'
import type { AnswerCardArtifact, PrintView } from '../types/recognition'
import { PrintableAnswerCard } from './PrintableAnswerCard'

export interface AnswerCardPrintHandle { prepare: () => Promise<void> }
interface PageManifest {
  artifact_id: string
  paper_instance_id: string
  version_no: number
  total_pages: number
  pages: Array<{ page_no: number }>
}

export const PrintableFrozenAnswerCard = forwardRef<AnswerCardPrintHandle, { data: PrintView }>(function PrintableFrozenAnswerCard({ data }, ref) {
  const [frozen, setFrozen] = useState<{ artifact: AnswerCardArtifact; urls: string[] } | null>(null)
  const resources = useRef<string[]>([])
  const controller = useRef<AbortController | null>(null)

  useEffect(() => () => {
    controller.current?.abort()
    resources.current.forEach(url => URL.revokeObjectURL(url))
  }, [])

  useImperativeHandle(ref, () => ({
    async prepare() {
      if (frozen || !data.is_active) return
      const abort = new AbortController()
      controller.current?.abort()
      controller.current = abort
      // Enter through the existing whole-paper print button. Older/disabled
      // deployments retain the current question + v2 answer-card print flow.
      let enabled = false
      try { enabled = (await apiGet<{ enabled: boolean }>('/internal/stage6/status', abort.signal)).enabled }
      catch (error) { if (abort.signal.aborted) throw error }
      if (!enabled) return
      const prefix = `/internal/stage6/paper-instances/${data.paper_instance_id}/answer-card-artifacts`
      const artifact = await apiPost<AnswerCardArtifact>(prefix, { expected_version_no: data.version_no }, abort.signal)
      const manifest = await apiGet<PageManifest>(artifact.manifest_url.replace(/^\/api\/v1/, ''), abort.signal)
      const numbers = manifest.pages.map(page => page.page_no).sort((a, b) => a - b)
      if (manifest.artifact_id !== artifact.id || manifest.paper_instance_id !== data.paper_instance_id
        || manifest.version_no !== data.version_no || manifest.total_pages !== artifact.total_pages
        || numbers.length !== artifact.total_pages || numbers.some((number, index) => number !== index + 1)) {
        throw new Error('答题卡页清单与试卷不一致，请刷新后重新打印')
      }
      const urls: string[] = []
      try {
        // Sequential loading bounds memory/network usage for long papers.
        for (const number of numbers) {
          const response = await fetch(apiUrl(`${prefix}/${artifact.id}/pages/${number}/image`), { signal: abort.signal })
          if (!response.ok) throw new Error(`答题卡第${number}页加载失败，请重新打印`)
          const url = URL.createObjectURL(await response.blob())
          urls.push(url)
          const image = new Image()
          image.src = url
          await image.decode()
          if (abort.signal.aborted) throw new Error('试卷已切换，请在当前试卷重新打印')
        }
        resources.current = urls
        flushSync(() => setFrozen({ artifact, urls }))
        await Promise.all(Array.from(document.querySelectorAll<HTMLImageElement>('.frozen-answer-page img')).map(image => image.decode()))
        await document.fonts.ready
      } catch (error) {
        urls.forEach(url => URL.revokeObjectURL(url))
        if (!abort.signal.aborted) setFrozen(null)
        throw error
      }
    },
  }), [data, frozen])

  if (!frozen) return <PrintableAnswerCard data={data} />
  return <section className="print-answer-zone print-frozen-answer-zone" aria-label="答题卡" data-artifact-code={frozen.artifact.artifact_code}>
    {frozen.urls.map((url, index) => <div className="frozen-answer-page" data-answer-page-no={index + 1} key={url}>
      <img src={url} alt={`答题卡 · 答题区域 · 第${index + 1}/${frozen.urls.length}页`} />
    </div>)}
  </section>
})
