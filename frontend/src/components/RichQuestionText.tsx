import { Image, Typography } from 'antd'
import { Fragment } from 'react'
import type { AssetInfo } from '../types'
import { ReadOnlyMath } from './ReadOnlyMath'

const richTokenPattern = /\[ASSET:([^\]]+)\]|\\\(([\s\S]*?)\\\)|\\\[([\s\S]*?)\\\]/g

export function RichQuestionText({ text, assets }: { text: string; assets: AssetInfo[] }) {
  const assetMap = new Map(assets.map((asset) => [asset.path, asset]))
  const segments: Array<{ type: 'text' | 'asset' | 'formula' | 'formula-block'; value: string }> = []
  let cursor = 0
  for (const match of text.matchAll(richTokenPattern)) {
    const index = match.index ?? 0
    if (index > cursor) segments.push({ type: 'text', value: text.slice(cursor, index) })
    if (match[1]) segments.push({ type: 'asset', value: match[1] })
    else if (match[2] !== undefined) segments.push({ type: 'formula', value: match[2] })
    else segments.push({ type: 'formula-block', value: match[3] })
    cursor = index + match[0].length
  }
  if (cursor < text.length) segments.push({ type: 'text', value: text.slice(cursor) })

  return (
    <Image.PreviewGroup>
      <Typography.Paragraph className="question-rich-text">
        {segments.map((segment, index) => {
          if (segment.type === 'text') return <Fragment key={index}>{segment.value}</Fragment>
          if (segment.type === 'formula' || segment.type === 'formula-block') {
            return <ReadOnlyMath key={index} latex={segment.value} block={segment.type === 'formula-block'} />
          }
          const asset = assetMap.get(segment.value)
          if (!asset?.web_url) {
            return <span key={index} className="asset-error">[图片或公式资源加载失败]</span>
          }
          const compact = /\.(wmf|emf)$/i.test(segment.value)
          return (
            <Image
              key={index}
              className={compact ? 'question-asset question-asset-compact' : 'question-asset question-asset-diagram'}
              preview={{ mask: '点击放大' }}
              src={asset.web_url}
              alt="题目图片或公式（点击放大）"
              fallback="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='28'%3E%3Ctext x='4' y='19' fill='%23c00'%3E图片或公式加载失败%3C/text%3E%3C/svg%3E"
            />
          )
        })}
      </Typography.Paragraph>
    </Image.PreviewGroup>
  )
}
