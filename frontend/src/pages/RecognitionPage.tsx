import { Alert, Button, Card } from 'antd'
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { apiGet } from '../api/client'
import { AnswerPageCollectionPanel } from '../components/AnswerPageCollectionPanel'

export function RecognitionPage() {
  const { paperInstanceId } = useParams()
  const [enabled, setEnabled] = useState<boolean | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [refresh, setRefresh] = useState(0)
  useEffect(() => {
    const abort = new AbortController()
    setEnabled(null); setError(null)
    apiGet<{ enabled: boolean }>('/internal/stage6/status', abort.signal)
      .then(value => { if (!abort.signal.aborted) setEnabled(value.enabled) })
      .catch(reason => { if (!abort.signal.aborted) setError((reason as Error).message) })
    return () => abort.abort()
  }, [refresh])
  if (error) return <Alert type="error" showIcon message="答题页功能加载失败" description={error}
    action={<Button onClick={() => setRefresh(v => v + 1)}>重试</Button>} />
  if (enabled === null) return <Card loading />
  if (!enabled) return <Alert type="warning" showIcon message="答题页识别尚未启用" description="请启用阶段六配置后刷新。"
    action={<Button><Link to={`/paper-instances/${paperInstanceId}/grading`}>查看批改</Link></Button>} />
  return paperInstanceId ? <AnswerPageCollectionPanel key={paperInstanceId} paperId={paperInstanceId} /> : null
}
