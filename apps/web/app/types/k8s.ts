export interface K8sClusterContext {
  workspace_id: string
  provider: 'gcp' | 'aws' | 'azure' | 'local' | 'kind' | string
  cluster_name: string
  context_name: string
  region: string
  status: 'connected' | 'degraded' | 'disconnected' | 'simulated'
  node_count: number
  control_plane_health: string
  k8s_version: string
  last_synced_at: string
  error_message?: string | null
  /** Workspace scaffold namespace (e.g. lp-django). */
  target_namespace?: string
}

export interface K8sEventItem {
  type: string
  reason: string
  message: string
  age: string
}

export interface K8sResource {
  id: string
  kind: 'Deployment' | 'Pod' | 'Service' | 'Ingress' | 'ConfigMap' | 'Secret' | string
  name: string
  namespace: string
  status: 'Running' | 'Pending' | 'CrashLoopBackOff' | 'Completed' | 'Error' | 'Terminating' | 'Active' | string
  ready_replicas: string
  age: string
  node?: string | null
  ip?: string | null
  ports: string[]
  endpoints: string[]
  created_at: string
  manifest_yaml: string
  events: K8sEventItem[]
}

export interface K8sPipelineStage {
  stage_id: 'manifest_parsed' | 'kube_api_accepted' | 'pods_provisioning' | 'ingress_ready'
  stage_name: string
  status: 'pending' | 'running' | 'success' | 'failed'
  timestamp: string
  message: string
  details?: Record<string, any>
}

export interface K8sDescribeMetadata {
  kind: string
  name: string
  namespace: string
  manifest_yaml: string
  events: K8sEventItem[]
  status: string
  age: string
  ip?: string
}

export interface K8sExecWsMessage {
  type: 'ready' | 'input' | 'resize' | 'error'
  data?: string
  cols?: number
  rows?: number
  message?: string
}
