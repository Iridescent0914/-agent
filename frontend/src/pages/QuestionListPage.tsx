import { DeleteOutlined, PlusOutlined, SearchOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Col, Empty, Form, Input, Modal, Row, Select, Space, Statistic, Table, Tag, Typography, Upload, message } from 'antd'
import type { ColumnsType, TablePaginationConfig } from 'antd/es/table'
import type { UploadFile } from 'antd/es/upload/interface'
import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { apiDelete, apiGet, apiPost, apiPostForm } from '../api/client'
import type { AssetInfo, Page, QuestionCatalogChapter, QuestionCatalogStats, QuestionCreateRequest, QuestionDetail, QuestionSummary } from '../types'
import type { ExamPoint } from '../types/profile'
import { MathTextEditor } from '../components/MathTextEditor'

interface QuestionCreateValues extends Omit<QuestionCreateRequest, 'options'> {
  options_text?: string
}

const typeLabel: Record<string, string> = {
  choice: '选择题',
  fill_blank: '填空题',
  calculation: '计算题',
  solution: '解答题',
  true_false: '判断题',
  unknown: '待确认题型',
}

function cleanPreview(value: string) {
  return value.replace(/\[ASSET:[^\]]+\]/g, '[公式]').replace(/\s+/g, ' ').slice(0, 100)
}

export function QuestionListPage() {
  const [createForm] = Form.useForm<QuestionCreateValues>()
  const [filterForm] = Form.useForm()
  const [searchParams, setSearchParams] = useSearchParams()
  const [data, setData] = useState<Page<QuestionSummary> | null>(null)
  const [stats, setStats] = useState<QuestionCatalogStats | null>(null)
  const [loading, setLoading] = useState(false)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [examPoints, setExamPoints] = useState<ExamPoint[]>([])
  const [catalog, setCatalog] = useState<QuestionCatalogChapter[]>([])
  const [questionImages, setQuestionImages] = useState<UploadFile[]>([])
  const [refreshKey, setRefreshKey] = useState(0)
  const page = Number(searchParams.get('page') || 1)
  const pageSize = Number(searchParams.get('page_size') || 20)
  const createType = Form.useWatch('question_type', createForm)
  const filterChapterCode = Form.useWatch('chapter_code', filterForm)
  const filterKnowledgePointCode = Form.useWatch('knowledge_point_code', filterForm)
  const filterKnowledgePoints = catalog.find((chapter) => chapter.code === filterChapterCode)?.knowledge_points || []
  const filterExamPoints = filterKnowledgePoints.find((point) => point.code === filterKnowledgePointCode)?.exam_points || []

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    apiGet<Page<QuestionSummary>>(`/questions?${searchParams.toString()}`, controller.signal)
      .then(setData)
      .catch((reason: Error) => {
        if (reason.name !== 'AbortError') setError(reason.message)
      })
      .finally(() => setLoading(false))
    return () => controller.abort()
  }, [searchParams, refreshKey])

  useEffect(() => {
    const controller = new AbortController()
    apiGet<QuestionCatalogStats>('/questions/stats', controller.signal)
      .then(setStats)
      .catch(() => undefined)
    return () => controller.abort()
  }, [refreshKey])

  useEffect(() => {
    apiGet<ExamPoint[]>('/exam-points?chapter_code=math.g6.rational')
      .then(setExamPoints)
      .catch((reason: Error) => setError(reason.message))
  }, [])

  useEffect(() => {
    apiGet<QuestionCatalogChapter[]>('/questions/catalog')
      .then(setCatalog)
      .catch((reason: Error) => setError(reason.message))
  }, [])

  async function createQuestion() {
    const values = await createForm.validateFields()
    setCreating(true); setError(null)
    try {
      const uploadedAssets: AssetInfo[] = []
      for (const image of questionImages) {
        if (!image.originFileObj) throw new Error(`无法读取图片：${image.name}`)
        const formData = new FormData()
        formData.append('file', image.originFileObj)
        uploadedAssets.push(await apiPostForm<AssetInfo>('/questions/assets', formData))
      }
      const options = values.question_type === 'choice'
        ? (values.options_text || '').split(/\r?\n/).map((value) => value.trim()).filter(Boolean)
        : undefined
      const created = await apiPost<QuestionDetail>('/questions', {
        ...values,
        options,
        options_text: undefined,
        original_qid: values.original_qid?.trim() || undefined,
        solution: values.solution?.trim() || undefined,
        wrong_analysis: values.wrong_analysis?.trim() || undefined,
        assets: uploadedAssets.map((asset) => ({ path: asset.path })),
      })
      message.success('题目已添加，请继续审核答案后加入自动组卷池')
      setCreateOpen(false)
      createForm.resetFields()
      setQuestionImages([])
      setRefreshKey((value) => value + 1)
      setSearchParams({ page: '1', page_size: String(pageSize), keyword: created.original_qid })
    } catch (reason) {
      setError((reason as Error).message)
    } finally {
      setCreating(false)
    }
  }

  function deleteQuestion(record: QuestionSummary) {
    Modal.confirm({
      title: `删除题目 ${record.original_qid}？`,
      content: '题目将退出题库和后续组卷；既有试卷快照与历史作答会保留。',
      okText: '确认删除', cancelText: '取消', okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await apiDelete(`/questions/${record.id}`, { operator_name: '任课教师' })
          message.success('题目已删除')
          setRefreshKey((value) => value + 1)
        } catch (reason) {
          setError((reason as Error).message)
        }
      },
    })
  }

  const columns: ColumnsType<QuestionSummary> = [
    {
      title: '题目',
      dataIndex: 'original_qid',
      width: 360,
      render: (_, record) => (
        <Space direction="vertical" size={2}>
          <Link to={`/questions/${record.id}`}><Typography.Text strong>{record.original_qid}</Typography.Text></Link>
          <Typography.Text type="secondary">{cleanPreview(record.question_text)}</Typography.Text>
        </Space>
      ),
    },
    { title: '题型', dataIndex: 'question_type', width: 90, render: (value) => typeLabel[value] || value },
    {
      title: '题库状态',
      dataIndex: 'grading_readiness',
      width: 145,
      render: (value, record) => value === 'machine_ready'
        ? <Tag color="green">自动组卷池</Tag>
        : <Space direction="vertical" size={2}>
            <Tag color="orange">待入库验证</Tag>
            {record.import_issue_count > 0 && <Typography.Text type="secondary">{record.import_issue_count} 项待处理</Typography.Text>}
          </Space>,
    },
    { title: '难度', dataIndex: 'difficulty', width: 100, render: (value: number) => '★'.repeat(value) },
    { title: '知识点', dataIndex: ['main_knowledge_point', 'name'], width: 170 },
    { title: '主考点', dataIndex: ['main_exam_point', 'name'], width: 180 },
    {
      title: '操作',
      key: 'action',
      width: 210,
      fixed: 'right',
      render: (_, record) => {
        const canEdit = record.question_type === 'choice' || record.question_type === 'fill_blank'
        const isPending = record.grading_readiness === 'raw_answer_only'
        const label = canEdit ? (isPending ? '去审核' : '撤回/编辑') : '查看'
        return <Space>
          <Link to={`/questions/${record.id}`}>
            <Button
              type={canEdit && isPending ? 'primary' : canEdit ? 'default' : 'link'}
              danger={canEdit && !isPending}
            >
              {label}
            </Button>
          </Link>
          <Button danger type='text' icon={<DeleteOutlined />} onClick={() => deleteQuestion(record)}>删除</Button>
        </Space>
      },
    },
  ]

  function update(values: Record<string, string | number | undefined>) {
    const next = new URLSearchParams(searchParams)
    Object.entries(values).forEach(([key, value]) => {
      if (value === undefined || value === '') next.delete(key)
      else next.set(key, String(value))
    })
    setSearchParams(next)
  }

  const pagination: TablePaginationConfig = {
    current: page,
    pageSize,
    total: data?.total || 0,
    showSizeChanger: true,
    showTotal: (total) => `共 ${total} 组题`,
    onChange: (nextPage, nextSize) => update({ page: nextPage, page_size: nextSize }),
  }

  return (
    <Space direction="vertical" size="large" className="page-stack">
      <div className='student-heading'>
        <div>
          <Typography.Title level={2}>题库</Typography.Title>
          <Typography.Text type='secondary'>可按章节、知识点和主考点逐级筛选；只有结构化答案可靠的题目才进入自动组卷池。</Typography.Text>
        </div>
        <Button type='primary' icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>上传新题</Button>
      </div>
      <Row gutter={16}>
        <Col xs={24} md={8}><Card><Statistic title="原始题库" value={stats?.total ?? 0} suffix="组" /></Card></Col>
        <Col xs={12} md={8}><Card><Statistic title="自动组卷池" value={stats?.ready_count ?? 0} suffix="组" valueStyle={{ color: '#237804' }} /></Card></Col>
        <Col xs={12} md={8}><Card><Statistic title="待入库验证" value={stats?.pending_count ?? 0} suffix="组" valueStyle={{ color: '#d46b08' }} /></Card></Col>
      </Row>
      <Alert
        type="info"
        showIcon
        message="自动组卷池与待入库题已隔离"
        description="待入库题可查看题干、原始参考答案和技术问题，但不会被推荐算法选入学生作业。"
      />
      <Card>
        <Form
          form={filterForm}
          layout="vertical"
          initialValues={{
            keyword: searchParams.get('keyword') || '',
            chapter_code: searchParams.get('chapter_code') || undefined,
            knowledge_point_code: searchParams.get('knowledge_point_code') || undefined,
            exam_point_code: searchParams.get('exam_point_code') || undefined,
            question_type: searchParams.get('question_type') || undefined,
            grading_readiness: searchParams.get('grading_readiness') || undefined,
            difficulty: searchParams.get('difficulty') ? Number(searchParams.get('difficulty')) : undefined,
          }}
          onFinish={(values) => update({ ...values, page: 1 })}
        >
          <Row gutter={16} align="bottom">
            <Col xs={24} md={6}><Form.Item name="keyword" label="关键词或 qid"><Input allowClear prefix={<SearchOutlined />} /></Form.Item></Col>
            <Col xs={12} md={4}><Form.Item name="chapter_code" label="章节"><Select allowClear options={catalog.map((chapter) => ({ value: chapter.code, label: chapter.name }))} onChange={() => filterForm.setFieldsValue({ knowledge_point_code: undefined, exam_point_code: undefined })} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name="knowledge_point_code" label="知识点"><Select allowClear disabled={!filterChapterCode} options={filterKnowledgePoints.map((point) => ({ value: point.code, label: point.name }))} onChange={() => filterForm.setFieldValue('exam_point_code', undefined)} /></Form.Item></Col>
            <Col xs={24} md={8}><Form.Item name="exam_point_code" label="主考点"><Select allowClear showSearch optionFilterProp="label" disabled={!filterKnowledgePointCode} options={filterExamPoints.map((point) => ({ value: point.code, label: point.name }))} /></Form.Item></Col>
          </Row>
          <Row gutter={16} align="bottom">
            <Col xs={12} md={6}><Form.Item name="grading_readiness" label="题库状态"><Select allowClear options={[{ value: 'machine_ready', label: '自动组卷池' }, { value: 'raw_answer_only', label: '待入库验证' }]} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name="question_type" label="题型"><Select allowClear options={Object.entries(typeLabel).map(([value, label]) => ({ value, label }))} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name="difficulty" label="难度"><Select allowClear options={[1, 2, 3, 4, 5].map((value) => ({ value, label: `${value} 星` }))} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item><Button type="primary" htmlType="submit" block>筛选</Button></Form.Item></Col>
          </Row>
        </Form>
      </Card>
      {error && <Alert type="error" showIcon message="题库加载失败" description={error} />}
      <Card className="table-card">
        <Table
          rowKey="id"
          columns={columns}
          dataSource={data?.items || []}
          loading={loading}
          pagination={pagination}
          locale={{ emptyText: <Empty description="没有符合条件的题目" /> }}
          scroll={{ x: 1090 }}
        />
      </Card>
      <Modal
        title='上传新题'
        width={760}
        open={createOpen}
        okText='保存到题库'
        cancelText='取消'
        confirmLoading={creating}
        onOk={() => void createQuestion()}
        onCancel={() => { setCreateOpen(false); createForm.resetFields(); setQuestionImages([]) }}
        destroyOnClose
      >
        <Alert
          type='info'
          showIcon
          message='新题先进入待审核区'
          description='选择题和填空题保存后可继续核对结构化答案，再确认进入自动组卷池。'
        />
        <Form
          form={createForm}
          layout='vertical'
          initialValues={{
            question_type: 'fill_blank',
            difficulty: 3,
            section: '教师新增',
            operator_name: '任课教师',
          }}
        >
          <Row gutter={16}>
            <Col xs={24} md={12}><Form.Item name='original_qid' label='题目编号（可留空自动生成）'><Input maxLength={180} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name='question_type' label='题型' rules={[{ required: true }]}><Select options={Object.entries(typeLabel).filter(([value]) => value !== 'unknown').map(([value, label]) => ({ value, label }))} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name='difficulty' label='难度' rules={[{ required: true }]}><Select options={[1, 2, 3, 4, 5].map((value) => ({ value, label: `${value} 星` }))} /></Form.Item></Col>
          </Row>
          <Row gutter={16}>
            <Col xs={24} md={12}><Form.Item name='main_exam_point_code' label='主考点' rules={[{ required: true }]}><Select showSearch optionFilterProp='label' options={examPoints.map((exam) => ({ value: exam.code, label: `${exam.name} · ${exam.code}` }))} /></Form.Item></Col>
            <Col xs={24} md={12}><Form.Item name='section' label='小节' rules={[{ required: true }]}><Input maxLength={255} /></Form.Item></Col>
          </Row>
          <Form.Item name='question_text' label='题干' rules={[{ required: true, whitespace: true }]}><MathTextEditor rows={5} maxLength={10000} /></Form.Item>
          <Form.Item
            label='题目图片（可选）'
            extra='支持 JPG、PNG、WebP，单张不超过 8MB，最多 8 张。图片会按顺序追加到题干末尾，详情页可点击放大。'
          >
            <Upload
              className='question-image-upload'
              accept='image/jpeg,image/png,image/webp'
              listType='picture-card'
              multiple
              maxCount={8}
              fileList={questionImages}
              beforeUpload={(file) => {
                if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) {
                  message.error('仅支持 JPG、PNG 或 WebP 图片')
                  return Upload.LIST_IGNORE
                }
                if (file.size > 8 * 1024 * 1024) {
                  message.error('单张图片不能超过 8MB')
                  return Upload.LIST_IGNORE
                }
                return false
              }}
              onChange={({ fileList }) => setQuestionImages(fileList)}
            >
              {questionImages.length < 8 && <div><PlusOutlined /><div style={{ marginTop: 8 }}>添加图片</div></div>}
            </Upload>
          </Form.Item>
          {createType === 'choice' && <Form.Item name='options_text' label='选项（每行一个）' rules={[{ required: true, whitespace: true }]}><Input.TextArea rows={4} placeholder={'例如：\nA. -1\nB. 0\nC. 1'} /></Form.Item>}
          <Form.Item name='reference_answer_raw' label='参考答案' rules={[{ required: true, whitespace: true }]}><Input.TextArea rows={3} maxLength={5000} showCount /></Form.Item>
          <Form.Item name='solution' label='解析（可选）'><Input.TextArea rows={3} maxLength={10000} /></Form.Item>
          <Form.Item name='wrong_analysis' label='易错分析（可选）'><Input.TextArea rows={2} maxLength={10000} /></Form.Item>
          <Form.Item name='operator_name' label='录入教师' rules={[{ required: true, whitespace: true }]}><Input maxLength={80} /></Form.Item>
        </Form>
      </Modal>
    </Space>
  )
}
