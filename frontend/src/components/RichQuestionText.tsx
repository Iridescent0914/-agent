import { Image, Typography } from 'antd'
import { Fragment } from 'react'
import type { AssetInfo } from '../types'

const assetPattern = /\[ASSET:([^\]]+)\]/g

export function RichQuestionText({ text, assets }: { text: string; assets: AssetInfo[] }) {
  const assetMap = new Map(assets.map((asset) => [asset.path, asset]))
  const segments: Array<{ type: 'text' | 'asset'; value: string }> = []
  let cursor = 0
  for (const match of text.matchAll(assetPattern)) {
    const index = match.index ?? 0
    if (index > cursor) segments.push({ type: 'text', value: text.slice(cursor, index) })
    segments.push({ type: 'asset', value: match[1] })
    cursor = index + match[0].length
  }
  if (cursor < text.length) segments.push({ type: 'text', value: text.slice(cursor) })

  return (
    <Typography.Paragraph className="question-rich-text">
      {segments.map((segment, index) => {
        if (segment.type === 'text') return <Fragment key={index}>{segment.value}</Fragment>
        const asset = assetMap.get(segment.value)
        if (!asset?.web_url) {
          return <span key={index} className="asset-error">[公式资源加载失败]</span>
        }
        return (
          <Image
            key={index}
            className="inline-formula"
            preview={false}
            src={asset.web_url}
            alt="题目公式"
            fallback="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' height='28'%3E%3Ctext x='4' y='19' fill='%23c00'%3E公式加载失败%3C/text%3E%3C/svg%3E"
          />
        )
      })}
    </Typography.Paragraph>
  )
}
