import { describe, expect, it } from 'vitest'
import {
  composeImageRef,
  parseInfraManifest,
  serializeInfraManifest,
  serviceUsesNodePort,
} from '../app/utils/infraManifestMapper'

const DEPLOYMENT_YAML = `apiVersion: apps/v1
kind: Deployment
metadata:
  name: app
  namespace: lp-demo
  labels:
    app: app
spec:
  replicas: 2
  selector:
    matchLabels:
      app: app
  template:
    metadata:
      labels:
        app: app
    spec:
      containers:
        - name: app
          image: nginx:1.27-alpine
          imagePullPolicy: IfNotPresent
          ports:
            - name: http
              containerPort: 80
          env:
            - name: ENVIRONMENT_NAME
              value: demo
          resources:
            requests:
              cpu: 100m
              memory: 128Mi
            limits:
              cpu: "500m"
              memory: 512Mi
`

const SERVICE_YAML = `apiVersion: v1
kind: Service
metadata:
  name: app
  namespace: lp-demo
  labels:
    app: app
spec:
  type: ClusterIP
  selector:
    app: app
  ports:
    - name: http
      port: 80
      targetPort: http
      protocol: TCP
`

describe('parseInfraManifest', () => {
  it('parses k8s deployment resources without hanging', () => {
    const started = Date.now()
    const model = parseInfraManifest('infra/k8s/manifests/deployment.yaml', DEPLOYMENT_YAML)
    expect(Date.now() - started).toBeLessThan(250)
    expect(model.kind).toBe('k8s-deployment')
    expect(model.resourceName).toBe('app')
    expect(model.appLabel).toBe('app')
    expect(model.replicas).toBe(2)
    expect(model.appImage).toBe('nginx')
    expect(model.imageTag).toBe('1.27-alpine')
    expect(model.pullPolicy).toBe('IfNotPresent')
    expect(model.appPort).toBe('80')
    expect(model.cpuRequest).toBe('100m')
    expect(model.memoryRequest).toBe('128Mi')
    expect(model.cpuLimit).toBe('500m')
    expect(model.memoryLimit).toBe('512Mi')
    expect(model.envVars).toEqual([{ key: 'ENVIRONMENT_NAME', value: 'demo' }])
  })

  it('stays fast on large manifests with many key/value lines', () => {
    const padding = Array.from({ length: 400 }, (_, i) => `          label${i}: value${i}`).join('\n')
    const large = `${DEPLOYMENT_YAML}\n${padding}\n`
    const started = Date.now()
    const model = parseInfraManifest('infra/k8s/manifests/deployment.yaml', large)
    expect(Date.now() - started).toBeLessThan(250)
    expect(model.memoryRequest).toBe('128Mi')
    expect(model.memoryLimit).toBe('512Mi')
  })

  it('parses and persists service type + named targetPort', () => {
    const model = parseInfraManifest('infra/k8s/manifests/service.yaml', SERVICE_YAML)
    expect(model.kind).toBe('k8s-service')
    expect(model.serviceType).toBe('ClusterIP')
    expect(model.targetPort).toBe('http')
    expect(model.appLabel).toBe('app')
    expect(model.nodePort).toBe('')

    model.serviceType = 'LoadBalancer'
    model.appLabel = 'web'
    model.targetPort = 'http'
    const next = serializeInfraManifest('infra/k8s/manifests/service.yaml', SERVICE_YAML, model)
    expect(next).toContain('type: LoadBalancer')
    expect(next).toContain('targetPort: http')
    expect(next).toMatch(/selector:\n\s+app: web/)
    expect(next.match(/type:\s*(ClusterIP|NodePort|LoadBalancer)/g)).toHaveLength(1)

    const roundTrip = parseInfraManifest('infra/k8s/manifests/service.yaml', next)
    expect(roundTrip.serviceType).toBe('LoadBalancer')
    expect(roundTrip.appLabel).toBe('web')
  })

  it('shows and persists nodePort only for NodePort / LoadBalancer', () => {
    const model = parseInfraManifest('infra/k8s/manifests/service.yaml', SERVICE_YAML)
    model.serviceType = 'NodePort'
    model.appPort = '80'
    model.targetPort = '5000'
    model.nodePort = '30081'
    const withNode = serializeInfraManifest('infra/k8s/manifests/service.yaml', SERVICE_YAML, model)
    expect(withNode).toContain('type: NodePort')
    expect(withNode).toContain('port: 80')
    expect(withNode).toContain('targetPort: 5000')
    expect(withNode).toContain('nodePort: 30081')

    const parsed = parseInfraManifest('infra/k8s/manifests/service.yaml', withNode)
    expect(parsed.nodePort).toBe('30081')
    expect(parsed.targetPort).toBe('5000')

    parsed.serviceType = 'ClusterIP'
    parsed.nodePort = '30081'
    const cleared = serializeInfraManifest('infra/k8s/manifests/service.yaml', withNode, parsed)
    expect(cleared).toContain('type: ClusterIP')
    expect(cleared).not.toMatch(/nodePort:/)
  })

  it('omits nodePort when left empty on NodePort (auto-assign)', () => {
    const model = parseInfraManifest('infra/k8s/manifests/service.yaml', SERVICE_YAML)
    model.serviceType = 'NodePort'
    model.nodePort = ''
    const yaml = `apiVersion: v1
kind: Service
metadata:
  name: app
spec:
  type: NodePort
  ports:
    - name: http
      port: 80
      targetPort: 80
      nodePort: 30080
`
    const next = serializeInfraManifest('infra/k8s/manifests/service.yaml', yaml, model)
    expect(next).toContain('type: NodePort')
    expect(next).not.toMatch(/nodePort:/)
  })

  it('updates deployment labels used for service linking', () => {
    const model = parseInfraManifest('infra/k8s/manifests/deployment.yaml', DEPLOYMENT_YAML)
    model.appLabel = 'frontend'
    model.replicas = 3
    const next = serializeInfraManifest('infra/k8s/manifests/deployment.yaml', DEPLOYMENT_YAML, model)
    expect(next).toContain('replicas: 3')
    expect(next.match(/^\s*app: frontend$/gm)?.length).toBeGreaterThanOrEqual(2)
  })

  it('preserves indented resources when updating image (no corrupt cpu block)', () => {
    const model = parseInfraManifest('infra/k8s/manifests/deployment.yaml', DEPLOYMENT_YAML)
    model.appImage = 'tiangolo/node-frontend'
    model.imageTag = 'latest'
    model.cpuRequest = '100m'
    model.memoryRequest = '128Mi'
    model.cpuLimit = '250m'
    model.memoryLimit = '256Mi'
    const next = serializeInfraManifest('infra/k8s/manifests/deployment.yaml', DEPLOYMENT_YAML, model)
    expect(next).toContain('image: tiangolo/node-frontend:latest')
    expect(next).toMatch(/^\s{10}resources:\n\s{12}requests:\n\s{14}cpu: 100m$/m)
    // Must remain valid nested YAML — unindented resources previously broke parse.
    expect(next).not.toMatch(/^resources:/m)
    expect(next).not.toMatch(/^  requests:/m)

    const roundTrip = parseInfraManifest('infra/k8s/manifests/deployment.yaml', next)
    expect(roundTrip.appImage).toBe('tiangolo/node-frontend')
    expect(roundTrip.imageTag).toBe('latest')
    expect(roundTrip.cpuRequest).toBe('100m')
    expect(roundTrip.cpuLimit).toBe('250m')
    expect(roundTrip.envVars).toEqual([{ key: 'ENVIRONMENT_NAME', value: 'demo' }])
  })

  it('does not truncate resources block at nested cpu keys across repeated saves', () => {
    let content = DEPLOYMENT_YAML
    for (let i = 0; i < 3; i += 1) {
      const model = parseInfraManifest('infra/k8s/manifests/deployment.yaml', content)
      model.cpuLimit = '250m'
      content = serializeInfraManifest('infra/k8s/manifests/deployment.yaml', content, model)
    }
    const cpuLines = content.match(/^\s+cpu:\s*.+$/gm) ?? []
    expect(cpuLines.length).toBe(2)
    expect(content.match(/^\s+resources:/gm)).toHaveLength(1)
  })
})

describe('composeImageRef / serviceUsesNodePort', () => {
  it('composes repo:tag unless tag already present', () => {
    expect(composeImageRef('nginx', '1.27')).toBe('nginx:1.27')
    expect(composeImageRef('nginx:1.27', 'latest')).toBe('nginx:1.27')
    expect(composeImageRef('', '1')).toBe('')
  })

  it('gates nodePort UI by service type', () => {
    expect(serviceUsesNodePort('ClusterIP')).toBe(false)
    expect(serviceUsesNodePort('NodePort')).toBe(true)
    expect(serviceUsesNodePort('LoadBalancer')).toBe(true)
  })
})

describe('helm values service ports', () => {
  const HELM_VALUES = `# values
replicaCount: 1
image:
  repository: nginx
  tag: "1.27-alpine"
  pullPolicy: IfNotPresent
service:
  type: ClusterIP
  port: 80
  targetPort: 80
resources:
  requests:
    cpu: 100m
    memory: 128Mi
  limits:
    cpu: 250m
    memory: 256Mi
env:
  ENVIRONMENT_NAME: demo
`

  it('parses and persists service.port / targetPort without breaking indentation', () => {
    const model = parseInfraManifest('infra/helm/app-chart/values.yaml', HELM_VALUES)
    expect(model.kind).toBe('helm-values')
    expect(model.appPort).toBe('80')
    expect(model.targetPort).toBe('80')
    expect(model.serviceType).toBe('ClusterIP')

    model.targetPort = '5000'
    model.appImage = 'bibi1989/afroshopclient'
    model.imageTag = '1.0'
    const next = serializeInfraManifest('infra/helm/app-chart/values.yaml', HELM_VALUES, model)
    expect(next).toMatch(/^service:\n(?:  .+\n)*  port: 80$/m)
    expect(next).toMatch(/^service:\n(?:  .+\n)*  targetPort: 5000$/m)
    expect(next).not.toMatch(/^port: /m)
    expect(next).toContain('repository: bibi1989/afroshopclient')

    const roundTrip = parseInfraManifest('infra/helm/app-chart/values.yaml', next)
    expect(roundTrip.targetPort).toBe('5000')
    expect(roundTrip.appPort).toBe('80')
  })

  it('repairs a corrupted unindented service.port line', () => {
    const broken = `service:
  type: ClusterIP
port: 80
  targetPort: 80
resources:
  requests:
    cpu: 100m
    memory: 128Mi
  limits:
    cpu: 250m
    memory: 256Mi
`
    const model = parseInfraManifest('infra/helm/app-chart/values.yaml', broken)
    model.targetPort = '5000'
    model.appPort = '80'
    model.cpuRequest = '100m'
    model.memoryRequest = '256Mi'
    model.cpuLimit = '500m'
    model.memoryLimit = '768Mi'
    const next = serializeInfraManifest('infra/helm/app-chart/values.yaml', broken, model)
    expect(next).toContain('  port: 80')
    expect(next).toContain('  targetPort: 5000')
    expect(next).not.toMatch(/^port: 80$/m)
    expect(next).not.toMatch(/^ {2}targetPort: 80$/m)
    expect(next.match(/^resources:/gm)).toHaveLength(1)
    expect(next).toContain('memory: 768Mi')

    // Round-trip stays valid YAML structure for helm.
    const again = serializeInfraManifest(
      'infra/helm/app-chart/values.yaml',
      next,
      parseInfraManifest('infra/helm/app-chart/values.yaml', next),
    )
    expect(again.match(/^resources:/gm)).toHaveLength(1)
  })
})
