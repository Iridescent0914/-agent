import { Fragment } from 'react'
import type { PrintSourceModule, PrintView } from '../types/recognition'

const ordinaryModules: PrintSourceModule[] = ['required', 'personalized', 'history']
const moduleNames: Record<PrintSourceModule, string> = {
  required: '必做题', personalized: '个性化', history: '历史回顾',
  weekly_summary: '周总结', unclassified: '未分类题目',
}
const kindNames: Record<string, string> = {
  choice: '选择题', fill_blank: '填空题', true_false: '判断题',
  calculation: '计算题', solution: '解答题',
}
const fieldNames = {
  choice: '选项', fill_blank: '答案', true_false: '判断结果', manual: '作答区',
}

export function PrintableAnswerCard({ data }: { data: PrintView }) {
  const modules = data.assignment_type === 'weekly_summary'
    ? ['weekly_summary' as const]
    : [...ordinaryModules]
  for (const module of ['weekly_summary', 'unclassified'] as const) {
    if (!modules.includes(module) && data.answer_slots.some(slot => slot.source_module === module)) {
      modules.push(module)
    }
  }
  return (
    <section className="print-answer-zone" aria-label="答题卡">
      <table className="answer-card-table" aria-label="学生答题卡">
        <thead>
          <tr><th>
            <div className="answer-card-heading">
              <div className="answer-card-title"><h2>答题卡</h2><span>{data.assignment_title}</span></div>
              <div className="answer-card-identity">
                <span>学生编号：{data.student_code}</span>
                <strong>试卷短码：{data.instance_code}</strong>
                <span>版本：v{data.version_no}</span>
              </div>
              <p>请按试卷题号，在对应小题和空位作答。</p>
            </div>
          </th></tr>
        </thead>
        <tbody>
          {modules.map((module, index) => {
            const slots = data.answer_slots.filter(slot => slot.source_module === module)
            return <Fragment key={module}>
              <tr className="answer-module-heading" data-source-module={module}><td>
                <h3>{['一', '二', '三', '四', '五'][index]}、{moduleNames[module]}</h3>
                {module === 'unclassified' && <p>历史试卷未保存题目模块，以下题目保留原题号。</p>}
              </td></tr>
              {!slots.length && <tr className="answer-module-empty"><td>本次无{moduleNames[module]}。</td></tr>}
              {slots.map(slot => <Fragment key={slot.order_no}>
                <tr className="answer-card-question" data-order-no={slot.order_no}><td>
                  <h4>第 {slot.order_no} 题<span>{kindNames[slot.kind] ?? '题目'} · {slot.subquestions.length} 小题 · {slot.blank_ids.length} 个作答位</span></h4>
                </td></tr>
                {slot.subquestions.map(subquestion => <tr className="answer-card-row" key={subquestion.subquestion_no}><td>
                  <div className="answer-subquestion">
                    <span className="answer-subquestion-number"><small>第 {slot.order_no} 题</small><strong>（{subquestion.subquestion_no}）</strong></span>
                    <div className="answer-blank-grid">
                      {subquestion.blanks.map(blank => <div
                        key={blank.item_id}
                        className={`answer-blank answer-blank-${blank.kind}`}
                        data-item-id={blank.item_id}
                        aria-label={`第${slot.order_no}题第${subquestion.subquestion_no}小题第${blank.blank_no}空`}
                      >
                        <span className="answer-blank-label">{subquestion.blanks.length > 1 ? `第${blank.blank_no}空 · ` : ''}{fieldNames[blank.kind]}</span>
                        <span className="answer-writing-space" />
                      </div>)}
                    </div>
                  </div>
                </td></tr>)}
              </Fragment>)}
            </Fragment>
          })}
        </tbody>
      </table>
    </section>
  )
}
