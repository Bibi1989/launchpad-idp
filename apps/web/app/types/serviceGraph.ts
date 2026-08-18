// Types mirroring the workspace service-graph API (schemas/repo_import.py).

export type CommProtocol =
  | 'kafka'
  | 'rabbitmq'
  | 'grpc'
  | 'redis'
  | 'http'
  | 'postgres'
  | 'mysql'
  | 'mariadb'
  | 'mongodb'

export type GraphNodeType = 'service' | 'broker' | 'datastore'

export interface ServiceGraphNode {
  id: string
  label: string
  type: GraphNodeType
  framework?: string | null
}

export interface ServiceGraphEdge {
  source: string
  target: string
  protocol: CommProtocol
  configured: boolean
}

export interface ServiceGraphResponse {
  repos: string[]
  nodes: ServiceGraphNode[]
  edges: ServiceGraphEdge[]
  mermaid: string
}

export interface ServiceConnection {
  source: string
  target: string
  protocol: CommProtocol
}
