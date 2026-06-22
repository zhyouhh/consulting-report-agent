import { test } from 'node:test'
import assert from 'node:assert/strict'
import { prepareProjectCreatePayload } from '../src/utils/projectCreatePayload.js'

test('payload omits workspace_dir and initial_material_paths (web)', () => {
  const p = prepareProjectCreatePayload({ theme: '战略分析', project_type: 'strategy', deadline: '2026-12-31', expected_length: '1万字' })
  assert.equal('workspace_dir' in p, false)
  assert.equal('initial_material_paths' in p, false)
  assert.equal(p.name, '战略分析')
})

test('payload still validates empty theme', () => {
  assert.throws(() => prepareProjectCreatePayload({ theme: '   ' }))
})
