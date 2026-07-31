import { describe, expect, it } from 'vitest'
import type { K8sResource } from '../app/types/k8s'

describe('Kubernetes Suite Data Structure Tests', () => {
  it('correctly filters resources by category kind', () => {
    const resources: K8sResource[] = [
      {
        id: 'res-1',
        kind: 'Deployment',
        name: 'web-api',
        namespace: 'default',
        status: 'Running',
        ready_replicas: '2/2',
        age: '5m',
        ports: ['8000/TCP'],
        endpoints: ['web-api.default.svc.cluster.local'],
        created_at: '2026-07-31T00:00:00Z',
        manifest_yaml: 'kind: Deployment',
        events: [],
      },
      {
        id: 'res-2',
        kind: 'Pod',
        name: 'web-api-pod-1',
        namespace: 'default',
        status: 'Running',
        ready_replicas: '1/1',
        age: '4m',
        ports: ['8000/TCP'],
        endpoints: [],
        created_at: '2026-07-31T00:00:00Z',
        manifest_yaml: 'kind: Pod',
        events: [],
      },
      {
        id: 'res-3',
        kind: 'Service',
        name: 'web-api-svc',
        namespace: 'default',
        status: 'Active',
        ready_replicas: '1/1',
        age: '6m',
        ports: ['80:8000/TCP'],
        endpoints: ['http://localhost:80'],
        created_at: '2026-07-31T00:00:00Z',
        manifest_yaml: 'kind: Service',
        events: [],
      },
    ]

    const deployments = resources.filter((r) => r.kind.toLowerCase().includes('deployment'))
    const pods = resources.filter((r) => r.kind.toLowerCase() === 'pod')
    const services = resources.filter((r) => r.kind.toLowerCase() === 'service')

    expect(deployments).toHaveLength(1)
    expect(deployments[0].name).toBe('web-api')
    expect(pods).toHaveLength(1)
    expect(pods[0].name).toBe('web-api-pod-1')
    expect(services).toHaveLength(1)
    expect(services[0].name).toBe('web-api-svc')
  })
})
