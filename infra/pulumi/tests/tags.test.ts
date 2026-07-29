import assert from 'node:assert/strict'
import test from 'node:test'
import { assertGovernanceTags, governanceLabels } from '../tags'

test('governanceLabels includes required keys', () => {
  const labels = governanceLabels({
    environmentId: 'env-1',
    owner: 'platform',
    createdBy: 'alice@example.com',
    ttlExpiration: '2026-07-30T00:00:00Z',
  })

  assert.equal(labels.EnvironmentId, 'env-1')
  assert.equal(labels.Owner, 'platform')
  assert.equal(labels.CreatedBy, 'alice@example.com')
  assert.equal(labels.TTL_Expiration, '2026-07-30T00:00:00Z')
})

test('assertGovernanceTags rejects missing fields', () => {
  assert.throws(() => assertGovernanceTags({ environmentId: 'x' }))
})
