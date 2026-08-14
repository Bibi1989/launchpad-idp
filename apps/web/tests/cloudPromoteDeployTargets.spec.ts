import { describe, expect, it } from 'vitest'
import { resolveCloudPromoteDeployTargets } from '../app/utils/cloudPromoteDeployTargets'

describe('resolveCloudPromoteDeployTargets', () => {
  it('maps aws vm promote to ec2 ecr vpc and sg', () => {
    const targets = resolveCloudPromoteDeployTargets({
      provider: 'aws',
      runtimeMode: 'running_instance',
      region: 'eu-central-1',
      networkMode: 'existing',
      existingVpcId: 'vpc-123',
      existingVpcLabel: 'app-vpc',
      securityGroupMode: 'auto',
      processStrategy: 'docker',
    })
    const titles = targets.map((t) => t.title)
    expect(titles).toContain('EC2')
    expect(titles).toContain('ECR')
    expect(titles.some((t) => t.includes('VPC'))).toBe(true)
    expect(titles).toContain('Security group')
  })

  it('maps gcp compose promote to cloud run', () => {
    const targets = resolveCloudPromoteDeployTargets({
      provider: 'gcp',
      runtimeMode: 'docker_compose',
      region: 'us-central1',
      networkMode: 'default',
    })
    expect(targets.some((t) => t.title === 'Cloud Run')).toBe(true)
    expect(targets.some((t) => t.title === 'Artifact Registry')).toBe(true)
  })
})
