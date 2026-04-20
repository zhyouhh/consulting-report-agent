/**
 * Pure logic for §9.4 rollback menu — stage-sensitive first-level option.
 * Extracted so node:test can import it without JSX.
 */

export const ROLLBACK_HIDDEN_STAGES = new Set(['S0', 'S1'])

/**
 * Returns the first-level rollback option for the given stage, or null.
 * @param {string} stageCode
 * @returns {{ label: string, checkpoint: string|null, action: string|null, confirmTitle?: string, confirmBody?: string } | null}
 */
export function getFirstLevelOption(stageCode) {
  switch (stageCode) {
    case 'S2':
    case 'S3':
      return { label: '调整大纲', checkpoint: null, action: null }
    case 'S4':
      return { label: '回到继续改的状态', checkpoint: null, action: null }
    case 'S5':
      return null // "回去再改" is the S5 secondary button (§9.2.2)
    case 'S6':
    case 'S7':
      return {
        label: '回到审查阶段',
        confirmTitle: '确认回到撰写阶段继续改报告？',
        confirmBody: '你写好的正文内容不会被删除，只是重新打开修改通道。',
        checkpoint: 'review-passed',
        action: 'clear',
      }
    default:
      return null
  }
}
