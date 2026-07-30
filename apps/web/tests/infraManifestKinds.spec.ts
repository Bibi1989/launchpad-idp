import { describe, expect, it } from 'vitest'
import {
  inferInfraManifestKind,
  parseInfraManifest,
  serializeInfraManifest,
} from '../app/utils/infraManifestMapper'

describe('inferInfraManifestKind', () => {
  it('maps important k8s manifest filenames', () => {
    expect(inferInfraManifestKind('infra/k8s/manifests/hpa.yaml')).toBe('k8s-hpa')
    expect(inferInfraManifestKind('infra/k8s/manifests/vpa.yaml')).toBe('k8s-vpa')
    expect(inferInfraManifestKind('infra/k8s/manifests/ingress.yaml')).toBe('k8s-ingress')
    expect(inferInfraManifestKind('infra/k8s/manifests/configmap.yaml')).toBe('k8s-configmap')
    expect(inferInfraManifestKind('infra/k8s/manifests/secret.yaml')).toBe('k8s-secret')
    expect(inferInfraManifestKind('infra/k8s/manifests/resourcequota.yaml')).toBe('k8s-resourcequota')
  })

  it('maps dependency datastore deployment filenames', () => {
    expect(inferInfraManifestKind('infra/k8s/manifests/postgres-deployment.yaml')).toBe('k8s-deployment')
    expect(inferInfraManifestKind('infra/k8s/manifests/redis-service.yaml')).toBe('k8s-service')
    expect(inferInfraManifestKind('infra/kustomize/deployment.yaml')).toBe('k8s-deployment')
  })
})

describe('hpa parse/serialize', () => {
  it('round-trips min/max/cpu', () => {
    const path = 'infra/k8s/manifests/hpa.yaml'
    const content = `
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: app
spec:
  minReplicas: 2
  maxReplicas: 10
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          averageUtilization: 70
`
    const model = parseInfraManifest(path, content)
    expect(model.kind).toBe('k8s-hpa')
    model.hpaMinReplicas = 3
    model.hpaMaxReplicas = 12
    model.hpaTargetCpu = 55
    const next = serializeInfraManifest(path, content, model)
    expect(next).toContain('minReplicas: 3')
    expect(next).toContain('maxReplicas: 12')
    expect(next).toContain('averageUtilization: 55')
  })
})
