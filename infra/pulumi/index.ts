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
const namespaceName = config.get('namespace') ?? `lp-${environmentId.slice(0, 8)}`

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
