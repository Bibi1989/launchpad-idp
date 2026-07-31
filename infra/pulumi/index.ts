import * as pulumi from '@pulumi/pulumi'
import * as gcp from '@pulumi/gcp'
import * as k8s from '@pulumi/kubernetes'
import { governanceLabels } from './tags'

const config = new pulumi.Config()
const project = gcp.config.project
const environmentId = config.require('environmentId')
const owner = config.require('owner')
const createdBy = config.require('createdBy')
const ttlExpiration = config.require('ttlExpiration')

function sanitizeDns1123(value: string, maxLen = 63, prefix = ''): string {
  let slug = value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/-+/g, '-').replace(/^-|-$/g, '')
  if (!slug) slug = 'env'
  let candidate = prefix ? `${prefix.replace(/-$/, '')}-${slug}` : (/^[a-z]/.test(slug) ? slug : `lp-${slug}`)
  candidate = candidate.slice(0, maxLen).replace(/-$/, '')
  if (!candidate || !/^[a-z]/.test(candidate)) {
    candidate = `lp-${candidate}`.slice(0, maxLen).replace(/-$/, '')
  }
  return candidate || 'lp-env'
}

const namespaceName = config.get('namespace') ?? sanitizeDns1123(environmentId, 63, 'lp')

const labels = governanceLabels({
  environmentId,
  owner,
  createdBy,
  ttlExpiration,
})

const secretId = config.get('dbSecretId') ?? 'launchpad-db-password'

export const dbPasswordSecret = gcp.secretmanager.getSecretVersionOutput({
  secret: secretId,
  project: project,
})

const namespace = new k8s.core.v1.Namespace(
  'environment-namespace',
  {
    metadata: {
      name: namespaceName,
      labels,
    },
  },
  { protect: false },
)

new k8s.networking.v1.NetworkPolicy(
  'deny-cross-namespace',
  {
    metadata: {
      name: 'deny-cross-namespace',
      namespace: namespace.metadata.name,
      labels,
    },
    spec: {
      podSelector: {},
      policyTypes: ['Ingress', 'Egress'],
      ingress: [
        {
          from: [
            {
              namespaceSelector: {
                matchLabels: {
                  'launchpad.io/environment-id': environmentId,
                },
              },
            },
          ],
        },
      ],
      egress: [
        {
          to: [
            {
              namespaceSelector: {
                matchLabels: {
                  'kubernetes.io/metadata.name': 'kube-system',
                },
              },
            },
          ],
        },
        {
          ports: [
            { protocol: 'UDP', port: 53 },
            { protocol: 'TCP', port: 53 },
          ],
        },
      ],
    },
  },
  { dependsOn: [namespace] },
)

new k8s.apiextensions.CustomResource(
  'secrets-store-csi',
  {
    apiVersion: 'secrets-store.csi.x-k8s.io/v1',
    kind: 'SecretProviderClass',
    metadata: {
      name: 'launchpad-secrets',
      namespace: namespace.metadata.name,
      labels,
    },
    spec: {
      provider: 'gcp',
      parameters: {
        secrets: JSON.stringify([
          {
            resourceName: pulumi.interpolate`projects/${project}/secrets/${secretId}/versions/latest`,
            path: 'db-password',
          },
        ]),
      },
    },
  },
  { dependsOn: [namespace] },
)

export const namespaceOutput = namespace.metadata.name
export const appliedLabels = labels
