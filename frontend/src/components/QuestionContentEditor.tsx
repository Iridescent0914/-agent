import { PlusOutlined } from '@ant-design/icons'
import { Alert, Button, Col, Form, Input, Modal, Row, Select, Upload, message } from 'antd'
import type { UploadFile } from 'antd/es/upload/interface'
import { useEffect, useState } from 'react'
import { apiDelete, apiGet, apiPostForm, apiPut } from '../api/client'
import type { AssetInfo, QuestionCatalogChapter, QuestionDetail, QuestionUpdateRequest } from '../types'
import type { ExamPoint } from '../types/profile'
import { MathTextEditor } from './MathTextEditor'

interface EditValues extends Omit<QuestionUpdateRequest, 'options' | 'expected_review_revision'> {
  options_text?: string
  chapter_code: string
}

const questionTypes = [
  { value: 'choice', label: '选择题' },
  { value: 'fill_blank', label: '填空题' },
  { value: 'calculation', label: '计算题' },
  { value: 'solution', label: '解答题' },
  { value: 'true_false', label: '判断题' },
]

function editableText(text: string) {
  return text.replace(/\n?\[ASSET:assets\/teacher\/[0-9a-f]{32}\.png\]/g, '').trim()
}

function optionsText(options: unknown): string {
  if (Array.isArray(options)) {
    return options.map((item) => typeof item === 'string' ? item : JSON.stringify(item)).join('\n')
  }
  if (options && typeof options === 'object') {
    return Object.entries(options as Record<string, unknown>)
      .map(([key, value]) => `${key}. ${typeof value === 'string' ? value : JSON.stringify(value)}`)
      .join('\n')
  }
  return ''
}

function existingFile(asset: AssetInfo): UploadFile {
  return {
    uid: `existing:${asset.path}`,
    name: asset.path.split('/').pop() || '题目图片',
    status: asset.web_url ? 'done' : 'error',
    url: asset.web_url || undefined,
  }
}

export function QuestionContentEditor({
  question,
  onUpdated,
}: {
  question: QuestionDetail
  onUpdated: (question: QuestionDetail) => void
}) {
  const [form] = Form.useForm<EditValues>()
  const [open, setOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [images, setImages] = useState<UploadFile[]>([])
  const [examPoints, setExamPoints] = useState<ExamPoint[]>([])
  const [chapters, setChapters] = useState<QuestionCatalogChapter[]>([])
  const questionType = Form.useWatch('question_type', form)
  const chapterCode = Form.useWatch('chapter_code', form)

  useEffect(() => {
    apiGet<QuestionCatalogChapter[]>('/questions/catalog')
      .then(setChapters)
      .catch(() => undefined)
  }, [])

  useEffect(() => {
    if (!chapterCode) return
    apiGet<ExamPoint[]>(`/exam-points?chapter_code=${encodeURIComponent(chapterCode)}`)
      .then(setExamPoints)
      .catch(() => undefined)
  }, [chapterCode])

  const begin = () => {
    form.setFieldsValue({
      original_qid: question.original_qid,
      chapter_code: question.chapter.code,
      question_type: question.question_type === 'unknown' ? 'fill_blank' : question.question_type,
      difficulty: question.difficulty,
      grade: question.grade,
      section: question.section,
      question_text: editableText(question.question_text),
      options_text: optionsText(question.options),
      reference_answer_raw: question.reference_answer_raw,
      main_exam_point_code: question.main_exam_point.code,
      related_exam_point_codes: question.related_exam_points.map((exam) => exam.code),
      solution: question.solution || undefined,
      wrong_analysis: question.wrong_analysis || undefined,
      estimated_score_rate: question.estimated_score_rate || undefined,
      core_literacy: question.core_literacy,
      is_required: question.is_required,
      operator_name: '任课教师',
    })
    setImages(question.assets.map(existingFile))
    setOpen(true)
  }

  const save = async () => {
    const values = await form.validateFields()
    const { chapter_code, ...questionValues } = values
    void chapter_code
    setSaving(true)
    const newlyUploaded: AssetInfo[] = []
    try {
      const assetPaths: string[] = []
      for (const image of images) {
        if (image.uid.startsWith('existing:')) {
          assetPaths.push(image.uid.slice('existing:'.length))
          continue
        }
        if (!image.originFileObj) throw new Error(`无法读取图片：${image.name}`)
        const body = new FormData()
        body.append('file', image.originFileObj)
        const uploaded = await apiPostForm<AssetInfo>('/questions/assets', body)
        newlyUploaded.push(uploaded)
        assetPaths.push(uploaded.path)
      }
      const options = values.question_type === 'choice'
        ? (values.options_text || '').split(/\r?\n/).map((item) => item.trim()).filter(Boolean)
        : undefined
      const updated = await apiPut<QuestionDetail>(`/questions/${question.id}`, {
        ...questionValues,
        options,
        options_text: undefined,
        solution: values.solution?.trim() || undefined,
        wrong_analysis: values.wrong_analysis?.trim() || undefined,
        assets: assetPaths.map((path) => ({ path })),
        expected_review_revision: question.review_revision,
      })
      const retained = new Set(assetPaths)
      await Promise.allSettled(question.assets
        .filter((asset) => asset.path.startsWith('assets/teacher/') && !retained.has(asset.path))
        .map((asset) => {
          const filename = asset.path.split('/').pop()
          return filename ? apiDelete(`/questions/assets/${filename}`) : Promise.resolve()
        }))
      onUpdated(updated)
      setOpen(false)
      message.success('题目内容已更新，请重新审核结构化答案')
    } catch (reason) {
      await Promise.allSettled(newlyUploaded.map((asset) => {
        const filename = asset.path.split('/').pop()
        return filename ? apiDelete(`/questions/assets/${filename}`) : Promise.resolve()
      }))
      message.error((reason as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return <>
    <Button onClick={begin}>编辑题目内容</Button>
    <Modal
      title="编辑题目内容"
      width={820}
      open={open}
      okText="保存并重新审核"
      cancelText="取消"
      confirmLoading={saving}
      onOk={() => void save()}
      onCancel={() => setOpen(false)}
      destroyOnClose
    >
      <Alert
        type="warning"
        showIcon
        message="内容修改后会退出自动组卷池"
        description="已有试卷继续使用原发布快照；当前题目的结构化答案会被清空，需要重新审核后才能再次入池。"
      />
      <Form form={form} layout="vertical">
        <Row gutter={16}>
          <Col xs={24} md={12}><Form.Item name="original_qid" label="题目编号" rules={[{ required: true, whitespace: true }]}><Input maxLength={180} /></Form.Item></Col>
          <Col xs={12} md={6}><Form.Item name="question_type" label="题型" rules={[{ required: true }]}><Select options={questionTypes} /></Form.Item></Col>
          <Col xs={12} md={6}><Form.Item name="difficulty" label="难度" rules={[{ required: true }]}><Select options={[1, 2, 3, 4, 5].map((value) => ({ value, label: `${value} 星` }))} /></Form.Item></Col>
        </Row>
        <Row gutter={16}>
          <Col xs={24} md={8}><Form.Item name="chapter_code" label="章节" rules={[{ required: true }]}><Select options={chapters.map((chapter) => ({ value: chapter.code, label: chapter.name }))} onChange={() => form.setFieldsValue({ main_exam_point_code: undefined, related_exam_point_codes: [] })} /></Form.Item></Col>
          <Col xs={24} md={8}><Form.Item name="main_exam_point_code" label="节内主属 KP" rules={[{ required: true }]}><Select showSearch optionFilterProp="label" options={examPoints.map((exam) => ({ value: exam.code, label: `${exam.name} · ${exam.code}` }))} /></Form.Item></Col>
          <Col xs={24} md={8}><Form.Item name="section" label="主属节" rules={[{ required: true, whitespace: true }]}><Input maxLength={255} /></Form.Item></Col>
        </Row>
        <Row gutter={16}>
          <Col xs={24} md={8}><Form.Item name="grade" label="年级" rules={[{ required: true, whitespace: true }]}><Input maxLength={80} /></Form.Item></Col>
          <Col xs={24} md={10}><Form.Item name="core_literacy" label="核心素养" rules={[{ required: true, whitespace: true }]}><Input maxLength={255} /></Form.Item></Col>
          <Col xs={24} md={6}><Form.Item name="is_required" label="是否为必做题" rules={[{ required: true }]}><Select options={[{ value: true, label: '是' }, { value: false, label: '否' }]} /></Form.Item></Col>
        </Row>
        <Form.Item name="related_exam_point_codes" label="关联知识点（可选）"><Select mode="multiple" showSearch optionFilterProp="label" options={examPoints.map((exam) => ({ value: exam.code, label: `${exam.name} · ${exam.code}` }))} /></Form.Item>
        <Form.Item name="question_text" label="题干" rules={[{ required: true, whitespace: true }]}><MathTextEditor rows={5} maxLength={10000} /></Form.Item>
        <Form.Item label="题目图片">
          <Upload
            accept="image/jpeg,image/png,image/webp"
            listType="picture-card"
            multiple
            maxCount={64}
            fileList={images}
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
            onChange={({ fileList }) => setImages(fileList)}
          >
            {images.length < 64 && <div><PlusOutlined /><div style={{ marginTop: 8 }}>添加图片</div></div>}
          </Upload>
        </Form.Item>
        {questionType === 'choice' && <Form.Item name="options_text" label="选项（每行一个）" rules={[{ required: true, whitespace: true }]}><Input.TextArea rows={4} /></Form.Item>}
        <Form.Item name="reference_answer_raw" label="参考答案" rules={[{ required: true, whitespace: true }]}><Input.TextArea rows={3} maxLength={5000} /></Form.Item>
        <Form.Item name="solution" label="解析"><MathTextEditor rows={3} maxLength={10000} /></Form.Item>
        <Form.Item name="wrong_analysis" label="易错分析"><Input.TextArea rows={2} maxLength={10000} /></Form.Item>
        <Form.Item name="estimated_score_rate" label="预估得分率"><Input maxLength={64} placeholder="例如 65% 或 0.65" /></Form.Item>
        <Form.Item name="operator_name" label="操作教师" rules={[{ required: true, whitespace: true }]}><Input maxLength={80} /></Form.Item>
      </Form>
    </Modal>
  </>
}
