/**
 * Pure logic for §9.4 rollback menu — stage-sensitive first-level option.
 * Extracted so node:test can import it without JSX.
 */

export const ROLLBACK_HIDDEN_STAGES = new Set(['S0', 'S1'])

// Option kinds so the menu component knows how to handle a click:
//   - 'insertPrompt'   : insert `prompt` text into the chat composer
//   - 'clearCheckpoint': POST checkpoint?action=clear after confirm dialog
//   - 'noop'           : informational only; just close the menu
export const OPTION_KIND_INSERT_PROMPT = 'insertPrompt'
export const OPTION_KIND_CLEAR_CHECKPOINT = 'clearCheckpoint'
export const OPTION_KIND_NOOP = 'noop'

/**
 * Returns the first-level rollback option for the given stage, or null.
 * Shape depends on kind:
 *   { kind: 'insertPrompt',    label, prompt }
 *   { kind: 'clearCheckpoint', label, checkpoint, action, confirmTitle, confirmBody }
 *   { kind: 'noop',            label }
 * @param {string} stageCode
 * @returns {object | null}
 */
export function getFirstLevelOption(stageCode) {
  switch (stageCode) {
    case 'S2':
    case 'S3':
      // §9.4: "让助手重新修 outline.md；写作通道保持开启" —
      // not clearing any checkpoint, just nudge the assistant via chat prompt.
      return {
        kind: OPTION_KIND_INSERT_PROMPT,
        label: '调整大纲',
        prompt: '请帮我重新修一下大纲',
      }
    case 'S4':
      // §9.4: "等价于不做任何事，作为心理提示"
      return {
        kind: OPTION_KIND_NOOP,
        label: '回到继续改的状态',
      }
    case 'S5':
      return null // "回去再改" is the S5 secondary button (§9.2.2)
    case 'S6':
    case 'S7':
      return {
        kind: OPTION_KIND_CLEAR_CHECKPOINT,
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

const S0_ROLLBACK_OPTION = {
  kind: OPTION_KIND_CLEAR_CHECKPOINT,
  label: '回到需求访谈',
  confirmTitle: '确认回到需求访谈？',
  confirmBody:
    '之前的表单信息不会删；回到 S0 继续补充澄清；' +
    '当前大纲、研究计划、数据日志等下游产出也会被清空。',
  checkpoint: 's0-interview-done',
  action: 'clear',
}

/**
 * Returns the advanced (secondary) rollback options for the given stage,
 * rendered AFTER the first-level option. Currently exposes:
 *   - s0 interview rollback for S2+ (cascades — clears all downstream
 *     checkpoints when the user confirms)
 * Empty array at S0 and S1 (menu is hidden there per ROLLBACK_HIDDEN_STAGES).
 * @param {string} stageCode
 * @returns {object[]}
 */
export function getAdvancedRollbackOptions(stageCode) {
  if (ROLLBACK_HIDDEN_STAGES.has(stageCode)) return []
  switch (stageCode) {
    case 'S2':
    case 'S3':
    case 'S4':
    case 'S5':
    case 'S6':
    case 'S7':
      return [S0_ROLLBACK_OPTION]
    default:
      return []
  }
}
