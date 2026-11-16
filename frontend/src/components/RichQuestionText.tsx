import { Image, Typography } from 'antd'
import { Fragment } from 'react'
import type { AssetInfo } from '../types'
import { ReadOnlyMath } from './ReadOnlyMath'

const richTokenPattern = /\[ASSET:([^\]]+)\]|\\\(([\s\S]*?)\\\)|\\\[([\s\S]*?)\\\]|\$\$([\s\S]*?)\$\$|(?<!\\)\$([^\n$]+?)\$/g
const bareLatexPattern = /\\{1,2}(?:dfrac|tfrac|frac|sqrt|times|div|cdot|leq|geq|neq|pi|alpha|beta|theta|angle|triangle|overline|dot|sum|int|pm|infty)(?:[A-Za-z0-9\\{}_^+\-*/=<>|().,])*/g
const leftDelimiterPattern = /\\{1,2}left(?:\\{1,2}[{}]|[()[\]{}|.])/g
const pairedDelimiterPattern = /\\{1,2}(left|right)(?:\\{1,2}[{}]|[()[\]{}|.])/g
const repeatingDecimalPattern = /\d+(?:\.\d+)?(?:\\{1,2}dot\{[^{}\n]+\})+/g
const powerPattern = /-?\s*(?:\([^()\n]+\)|[A-Za-z0-9]+)\s*\^\s*(?:\{[^{}\n]+\}|[A-Za-z0-9]+)/g
const fractionExpressionPattern = /(?:\([+-]?\d+(?:\.\d+)?(?:\s+\d+\/\d+|\/\d+)?\)|[+-]?\d+(?:\s+\d+)?\/\d+)(?:\s*[+-]\s*(?:\([+-]?\d+(?:\.\d+)?(?:\s+\d+\/\d+|\/\d+)?\)|[+-]?\d+(?:\s+\d+)?\/\d+))*/g

type RichSegment = { type: 'text' | 'asset' | 'formula' | 'formula-block'; value: string }
type RichBlock = { type: 'text'; value: string } | { type: 'table'; headers: string[]; rows: string[][] }

function normalizeLatex(value: string): string {
  // Some model responses contain two literal backslashes before a command.
  return value.replace(/\\\\(?=[A-Za-z])/g, '\\')
}

function normalizePlainMath(value: string): string {
  return normalizeLatex(value)
    .replace(/(\d+)\s+(\d+)\s*\/\s*(\d+)/g, '$1\\frac{$2}{$3}')
    .replace(/(\d+)\s*\/\s*(\d+)/g, '\\frac{$1}{$2}')
}

function nextPairedMath(value: string, cursor: number): { at: number; raw: string } | null {
  leftDelimiterPattern.lastIndex = cursor
  let left: RegExpExecArray | null
  while ((left = leftDelimiterPattern.exec(value))) {
    // A broken pair must not consume the next subquestion's math.
    const lineEnd = value.indexOf('\n', left.index)
    const limit = lineEnd < 0 ? value.length : lineEnd
    pairedDelimiterPattern.lastIndex = left.index
    let depth = 0
    let delimiter: RegExpExecArray | null
    while ((delimiter = pairedDelimiterPattern.exec(value)) && delimiter.index < limit) {
      depth += delimiter[1] === 'left' ? 1 : -1
      if (depth === 0) {
        return { at: left.index, raw: value.slice(left.index, pairedDelimiterPattern.lastIndex) }
      }
    }
  }
  return null
}

function splitBareMath(value: string, mathFragments: string[]): RichSegment[] {
  const result: RichSegment[] = []
  const fragments = [...new Set(mathFragments.flatMap((fragment) => [fragment, normalizeLatex(fragment)]))]
    .filter((fragment) => /[\\^/]/.test(fragment))
  let cursor = 0
  while (cursor < value.length) {
    let foundAt = value.length
    let found = ''
    let latex = ''
    const consider = (at: number, raw: string, normalized: string) => {
      if (at >= cursor && (at < foundAt || (at === foundAt && raw.length > found.length))) {
        foundAt = at
        found = raw
        latex = normalized
      }
    }
    for (const fragment of fragments) {
      const at = value.indexOf(fragment, cursor)
      if (at >= 0) consider(at, fragment, normalizePlainMath(fragment))
    }
    const paired = nextPairedMath(value, cursor)
    if (paired) consider(paired.at, paired.raw, normalizeLatex(paired.raw))
    for (const pattern of [repeatingDecimalPattern, bareLatexPattern, powerPattern, fractionExpressionPattern]) {
      pattern.lastIndex = cursor
      let match: RegExpExecArray | null
      while ((match = pattern.exec(value))) {
        const raw = pattern === bareLatexPattern ? match[0].replace(/[.,]+$/, '') : match[0]
        if (pattern === fractionExpressionPattern && !raw.includes('/')) continue
        consider(match.index, raw, pattern === bareLatexPattern ? normalizeLatex(raw) : normalizePlainMath(raw))
        break
      }
    }
    if (!found) break
    if (foundAt > cursor) result.push({ type: 'text', value: value.slice(cursor, foundAt) })
    result.push({ type: 'formula', value: latex })
    cursor = foundAt + found.length
  }
  if (cursor < value.length) result.push({ type: 'text', value: value.slice(cursor) })
  return result
}

function tableCells(line: string): string[] | null {
  const trimmed = line.trim()
  if (!trimmed.includes('|')) return null
  const cells = trimmed.replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => cell.trim())
  return cells.length > 1 ? cells : null
}

function richBlocks(text: string): RichBlock[] {
  const lines = text.split(/\r?\n/)
  const blocks: RichBlock[] = []
  let plain: string[] = []
  const flush = () => {
    if (plain.length) blocks.push({ type: 'text', value: plain.join('\n') })
    plain = []
  }
  for (let index = 0; index < lines.length; index += 1) {
    const headers = tableCells(lines[index])
    const separator = index + 1 < lines.length ? tableCells(lines[index + 1]) : null
    if (headers && separator?.length === headers.length && separator.every((cell) => /^:?-{3,}:?$/.test(cell))) {
      const rows: string[][] = []
      let next = index + 2
      while (next < lines.length) {
        const cells = tableCells(lines[next])
        if (!cells || cells.length !== headers.length) break
        rows.push(cells)
        next += 1
      }
      if (rows.length) {
        flush()
        blocks.push({ type: 'table', headers, rows })
        index = next - 1
        continue
      }
    }
    plain.push(lines[index])
  }
  flush()
  return blocks
}

function inlineSegments(text: string, mathFragments: string[]): RichSegment[] {
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
  return segments.flatMap((segment) => segment.type === 'text' ? splitBareMath(segment.value, mathFragments) : [segment])
}

export function RichQuestionText({ text, assets, mathFragments = [] }: { text: string; assets: AssetInfo[]; mathFragments?: string[] }) {
  const assetMap = new Map(assets.map((asset) => [asset.path, asset]))
  const renderInline = (value: string) => inlineSegments(value, mathFragments).map((segment, index) => {
    if (segment.type === 'text') return <Fragment key={index}>{segment.value}</Fragment>
    if (segment.type === 'formula' || segment.type === 'formula-block') {
      return <ReadOnlyMath key={index} latex={segment.value} block={segment.type === 'formula-block'} />
    }
    const asset = assetMap.get(segment.value)
    if (!asset?.web_url) return <span key={index} className="asset-error">[图片或公式资源加载失败]</span>
    const compact = /\.(wmf|emf)$/i.test(segment.value)
    return <Image key={index}
      className={compact ? 'question-asset question-asset-compact' : 'question-asset question-asset-diagram'}
      preview={{ mask: '点击放大' }} src={asset.web_url} alt="题目图片或公式（点击放大）"
      fallback="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='28'%3E%3Ctext x='4' y='19' fill='%23c00'%3E图片或公式加载失败%3C/text%3E%3C/svg%3E" />
  })
  return (
    <Image.PreviewGroup>
      {richBlocks(text).map((block, index) => block.type === 'table'
        ? <div className="question-markdown-table-wrap" key={index}><table className="question-markdown-table">
          <thead><tr>{block.headers.map((cell, cellIndex) => <th key={cellIndex}>{renderInline(cell)}</th>)}</tr></thead>
          <tbody>{block.rows.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex}>{renderInline(cell)}</td>)}</tr>)}</tbody>
        </table></div>
        : <Typography.Paragraph key={index} className="question-rich-text">{renderInline(block.value)}</Typography.Paragraph>)}
    </Image.PreviewGroup>
  )
}
