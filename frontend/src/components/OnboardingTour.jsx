import React, { useState } from 'react'
import axios from 'axios'

// 初次使用引导（2026-07-11，终身一次）：authUser.onboarded === false 时由 App 渲染；
// 完成或跳过都 POST /api/auth/onboarded 回写（users.onboarded_at），换设备/浏览器不再弹。
// 居中卡片式（不做元素高亮定位）：桌面三栏与移动抽屉壳零布局耦合，两端通用。
// 本文件注释与文案不得出现表情符号（paletteGuard 扫描）。
const STEPS = [
  {
    title: '三个工作区',
    body: '左侧是项目列表；中间与助手对话；右侧工作台有「阶段」「文件」「材料」三个标签，报告写作的全部产出都在这里。',
  },
  {
    title: '从需求访谈开始',
    body: '新建报告后，助手会先向你确认需求，再按「需求确认、拟大纲、调研、分析、写作、审查、交付」的阶段推进。「阶段」标签可以看到进度和推进按钮。',
  },
  {
    title: '随时查看与修改产出',
    body: '大纲、报告正文等文件在「文件」标签里实时可看、可直接编辑；聊天里出现的文件名也可以点击直达。',
  },
  {
    title: '审查与导出',
    body: '正文完成后，在「阶段」标签用「独立审查」按钮做质量审查；通过后点「导出可审草稿」下载 Word 文档。',
  },
]

export default function OnboardingTour({ onDone }) {
  const [step, setStep] = useState(0)
  const [submitting, setSubmitting] = useState(false)

  const finish = async () => {
    if (submitting) return
    setSubmitting(true)
    try {
      await axios.post('/api/auth/onboarded')
    } catch {
      // 回写失败不挡使用：本次会话由 onDone 更新本地 authUser 不再弹，下次登录自动补标记
    } finally {
      onDone()
    }
  }

  const isLast = step === STEPS.length - 1
  const current = STEPS[step]

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-scrim/45 dark:bg-scrim/60">
      <div className="bg-card rounded-win border border-border shadow-popover p-6 w-[min(440px,calc(100vw-32px))]">
        <div className="text-xs text-t3 mb-2">初次使用引导 {step + 1}/{STEPS.length}</div>
        <h2 className="text-lg font-semibold text-text mb-2">{current.title}</h2>
        <p className="text-sm text-t2 leading-relaxed mb-5">{current.body}</p>
        <div className="flex items-center gap-1.5 mb-5">
          {STEPS.map((_, i) => (
            <span
              key={i}
              className={`h-1.5 rounded-full transition-all ${i === step ? 'w-5 bg-accent' : 'w-1.5 bg-dotfuture'}`}
            />
          ))}
        </div>
        <div className="flex gap-2 items-center">
          <button
            onClick={finish}
            disabled={submitting}
            className="px-3 py-2 text-sm text-t2 hover:text-text disabled:opacity-60"
          >
            跳过
          </button>
          <div className="flex-1" />
          {step > 0 && (
            <button
              onClick={() => setStep(step - 1)}
              className="px-4 py-2 text-sm border border-border text-text rounded-btn hover:bg-card2"
            >
              上一步
            </button>
          )}
          {isLast ? (
            <button
              onClick={finish}
              disabled={submitting}
              className="px-4 py-2 text-sm bg-accent text-white rounded-btn hover:bg-accent/90 disabled:opacity-60"
            >
              开始使用
            </button>
          ) : (
            <button
              onClick={() => setStep(step + 1)}
              className="px-4 py-2 text-sm bg-accent text-white rounded-btn hover:bg-accent/90"
            >
              下一步
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
