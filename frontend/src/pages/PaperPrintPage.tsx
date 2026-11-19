import { ArrowLeftOutlined, PrinterOutlined } from '@ant-design/icons'
import { Alert, Button, Descriptions, Skeleton, Typography } from 'antd'
import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { apiGet } from '../api/client'
import { RichQuestionText } from '../components/RichQuestionText'
import { QuestionOptions } from '../components/QuestionOptions'
import { PrintableFrozenAnswerCard, type AnswerCardPrintHandle } from '../components/PrintableFrozenAnswerCard'
import type { PrintView } from '../types/recognition'

export function PaperPrintPage() {
  const { paperInstanceId } = useParams()
  const [data, setData] = useState<PrintView | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [printing, setPrinting] = useState(false)
  const [printError, setPrintError] = useState<string | null>(null)
  const answerCard = useRef<AnswerCardPrintHandle>(null)
  const paperScope = useRef(paperInstanceId)
  paperScope.current = paperInstanceId
  useEffect(() => {
    if (!paperInstanceId) return
    const controller = new AbortController()
    setData(null)
    setError(null)
    setPrintError(null)
    setPrinting(false)
    apiGet<PrintView>(`/paper-instances/${paperInstanceId}/print-view`, controller.signal)
      .then(value => { if (!controller.signal.aborted) setData(value) })
      .catch((reason: Error) => { if (!controller.signal.aborted) setError(reason.message) })
    return () => controller.abort()
  }, [paperInstanceId])
  async function printPaper() {
    const scope = paperScope.current
    setPrinting(true)
    setPrintError(null)
    try {
      await answerCard.current?.prepare()
      if (scope !== paperScope.current) return
      await Promise.all(Array.from(document.querySelectorAll<HTMLImageElement>('.print-question img')).map(image => image.decode()))
      if (scope !== paperScope.current) return
      window.print()
    } catch (reason) {
      if (scope === paperScope.current) setPrintError(reason instanceof Error ? reason.message : '打印准备失败，请刷新后重试')
    } finally {
      if (scope === paperScope.current) setPrinting(false)
    }
  }
  if (error) return <Alert type="error" showIcon message="打印视图加载失败" description={error} />
  if (!data) return <Skeleton active />
  return <div className="paper-print-page">
    <div className="print-toolbar"><Button type="link" icon={<ArrowLeftOutlined />}><Link to={`/paper-instances/${data.paper_instance_id}`}>返回试卷</Link></Button><Button type="primary" icon={<PrinterOutlined />} loading={printing} onClick={printPaper}>打印 / 保存 PDF</Button></div>
    {printError && <Alert className="print-error-alert" type="error" showIcon message={printError} />}
    {!data.is_active && <Alert className="print-history-alert" type="warning" showIcon message="这是历史版本，请按本页短码收取答案" />}
    <header className="print-header"><Typography.Title level={2}>{data.assignment_title}</Typography.Title><Descriptions size="small" column={3}><Descriptions.Item label="学生编号">{data.student_code}</Descriptions.Item><Descriptions.Item label="版本">v{data.version_no}</Descriptions.Item><Descriptions.Item label="试卷短码">{data.instance_code}</Descriptions.Item></Descriptions></header>
    <main>{data.items.map((item) => <section className="print-question" key={item.id}><Typography.Title level={5}>第 {item.order_no} 题</Typography.Title><RichQuestionText text={String(item.question_snapshot.question_text ?? '')} assets={item.assets} />{item.options_snapshot != null && <QuestionOptions options={item.options_snapshot} assets={item.assets} />}</section>)}</main>
    <PrintableFrozenAnswerCard data={data} ref={answerCard} key={`${data.paper_instance_id}:${data.version_no}`} />
  </div>
}
