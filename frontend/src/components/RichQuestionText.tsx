import { Image, Typography } from 'antd'
import { Fragment } from 'react'
import type { AssetInfo } from '../types'
import { ReadOnlyMath } from './ReadOnlyMath'

const richTokenPattern = /\[ASSET:([^\]]+)\]|\\\(([\s\S]*?)\\\)|\\\[([\s\S]*?)\\\]|\$\$([\s\S]*?)\$\$|(?<!\\)\$([^\n$]+?)\$/g
const bareLatexPattern = /\\{1,2}(?:dfrac|tfrac|frac|sqrt|times|div|cdot|leq|geq|neq|pi|alpha|beta|theta|angle|triangle|overline|sum|int|pm|infty)(?:[A-Za-z0-9\\{}_^+\-*/=<>|().,])*/g

type RichSegment = { type: 'text' | 'asset' | 'formula' | 'formula-block'; value: string }

function normalizeLatex(value: string): string {
  // Some model responses contain two literal backslashes before a command.
  return value.replace(/\\\\(?=[A-Za-z])/g, '\\')
}

function splitBareMath(value: string, mathFragments: string[]): RichSegment[] {
  const result: RichSegment[] = []
  const fragments = [...new Set(mathFragments.flatMap((fragment) => [fragment, normalizeLatex(fragment)]))]
    .filter((fragment) => fragment.includes('\\'))
  let cursor = 0
  while (cursor < value.length) {
    let foundAt = value.length
    let found = ''
    for (const fragment of fragments) {
      const at = value.indexOf(fragment, cursor)
      if (at >= 0 && (at < foundAt || (at === foundAt && fragment.length > found.length))) {
        foundAt = at
        found = fragment
      }
    }
    bareLatexPattern.lastIndex = cursor
    const fallback = bareLatexPattern.exec(value)
    if (fallback && (fallback.index < foundAt || (fallback.index === foundAt && fallback[0].length > found.length))) {
      foundAt = fallback.index
      found = fallback[0].replace(/[.,]+$/, '')
    }
    if (!found) break
    if (foundAt > cursor) result.push({ type: 'text', value: value.slice(cursor, foundAt) })
    result.push({ type: 'formula', value: normalizeLatex(found) })
    cursor = foundAt + found.length
  }
  if (cursor < value.length) result.push({ type: 'text', value: value.slice(cursor) })
  return result
}

export function RichQuestionText({ text, assets, mathFragments }: { text: string; assets: AssetInfo[]; mathFragments?: string[] }) {
  const assetMap = new Map(assets.map((asset) => [asset.path, asset]))
  const segments: RichSegment[] = []
  let cursor = 0
  for (const match of text.matchAll(richTokenPattern)) {
    const index = match.index ?? 0
    if (index > cursor) segments.push({ type: 'text', value: text.slice(cursor, index) })
    if (match[1]) segments.push({ type: 'asset', value: match[1] })
    else if (match[2] !== undefined || match[5] !== undefined) {
      segments.push({ type: 'formula', value: match[2] ?? match[5] })
    } else {
      segments.push({ type: 'formula-block', value: match[3] ?? match[4] })
    }
    cursor = index + match[0].length
  }
  if (cursor < text.length) segments.push({ type: 'text', value: text.slice(cursor) })
  const displaySegments = mathFragments
    ? segments.flatMap((segment) => segment.type === 'text' ? splitBareMath(segment.value, mathFragments) : [segment])
    : segments

  return (
    <Image.PreviewGroup>
      <Typography.Paragraph className="question-rich-text">
        {displaySegments.map((segment, index) => {
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
