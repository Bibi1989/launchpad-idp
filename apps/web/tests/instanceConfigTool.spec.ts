import { describe, expect, it } from 'vitest'
import { ansibleWantedForWorkspace } from '../app/utils/cloudValidation'

describe('ansibleWantedForWorkspace', () => {
  it('defaults to LaunchConfig (no Ansible)', () => {
    expect(
      ansibleWantedForWorkspace({
        ansibleEnabled: false,
        iacEngine: 'launchpad',
        configTool: 'cloud-init',
      }),
    ).toBe(false)
  })

  it('enables Ansible when the config tool is ansible', () => {
    expect(
      ansibleWantedForWorkspace({
        ansibleEnabled: false,
        iacEngine: 'pulumi',
        configTool: 'ansible',
      }),
    ).toBe(true)
  })

  it('enables Ansible when the IaC engine is ansible', () => {
    expect(
      ansibleWantedForWorkspace({
        ansibleEnabled: false,
        iacEngine: 'ansible',
        configTool: 'cloud-init',
      }),
    ).toBe(true)
  })
})
