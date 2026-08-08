import { describe, expect, it } from 'vitest'
import {
  applyInstanceComputeTarget,
  instanceComputeTargetsForProvider,
  resolveSelectedInstanceComputeTarget,
} from '~/utils/instanceComputeTargets'
import { defaultRunningInstanceConfig } from '~/utils/workspaceRuntimeMode'

describe('instanceComputeTargets', () => {
  it('lists Docker container services per cloud', () => {
    expect(instanceComputeTargetsForProvider('gcp').map((t) => t.id)).toEqual([
      'gcp_cloud_run',
      'gcp_vm_ssh',
    ])
    expect(instanceComputeTargetsForProvider('aws').map((t) => t.id)).toEqual([
      'aws_app_runner',
      'aws_ec2',
    ])
    expect(instanceComputeTargetsForProvider('azure').map((t) => t.id)).toEqual([
      'azure_container_apps',
      'azure_vm_ssh',
    ])
  })

  it('enables cloud_run and sets serverless when Cloud Run is selected', () => {
    const resources: Record<string, unknown> = { cloud_run: false, region: 'europe-west1' }
    const next = applyInstanceComputeTarget({
      provider: 'gcp',
      targetId: 'gcp_cloud_run',
      runningInstance: defaultRunningInstanceConfig('gcp'),
      resources,
    })
    expect(resources.cloud_run).toBe(true)
    expect(next.kind).toBe('serverless')
    expect(next.region).toBe('europe-west1')
  })

  it('enables app_runner for AWS serverless and clears ec2', () => {
    const resources: Record<string, unknown> = { app_runner: false, ec2: true }
    const next = applyInstanceComputeTarget({
      provider: 'aws',
      targetId: 'aws_app_runner',
      runningInstance: defaultRunningInstanceConfig('aws'),
      resources,
    })
    expect(resources.app_runner).toBe(true)
    expect(resources.ec2).toBe(false)
    expect(next.kind).toBe('serverless')
  })

  it('resolves selected target from resources + kind', () => {
    const selected = resolveSelectedInstanceComputeTarget(
      'aws',
      { ...defaultRunningInstanceConfig('aws'), kind: 'vm' },
      { ec2: true, app_runner: false },
    )
    expect(selected?.id).toBe('aws_ec2')
  })
})
