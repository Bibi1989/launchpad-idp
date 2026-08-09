import { describe, expect, it } from 'vitest'
import {
  buildAnsibleScaffold,
  frameworksFromContainerServices,
} from '../app/utils/ansibleScaffold'
import { defaultAnsibleConfig } from '../app/utils/cloudValidation'

describe('ansibleScaffold', () => {
  it('dedupes frameworks from workspace services', () => {
    expect(
      frameworksFromContainerServices([
        { name: 'web', app_kind: 'frontend', stack: 'nextjs', listen_port: 3000 },
        { name: 'api', app_kind: 'backend', stack: 'fastapi', listen_port: 8000 },
        { name: 'web2', app_kind: 'frontend', stack: 'nextjs', listen_port: 3001 },
      ]),
    ).toEqual(['nextjs', 'fastapi'])
  })

  it('writes Ansible tree under infra/ansible', () => {
    const files = buildAnsibleScaffold('demo', {
      ...defaultAnsibleConfig(),
      enabled: true,
      hosts: '10.0.0.5',
    })
    const paths = files.map((f) => f.path)
    expect(paths.every((p) => p.startsWith('infra/ansible/'))).toBe(true)
    expect(paths).toContain('infra/ansible/playbooks/site.yml')
    expect(paths).toContain('infra/ansible/inventory/hosts.yml')
    const inventory = files.find((f) => f.path.endsWith('inventory/hosts.yml'))
    expect(inventory?.content).toContain('10.0.0.5')
  })
})
