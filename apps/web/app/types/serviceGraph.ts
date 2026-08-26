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
  /** Raw operator-configured connectors (incl. kind/expose_as/cors_origin) for hydration. */
  connectors?: ServiceConnection[]
}

export type ConnectorKind = 'service' | 'cors'

export interface ServiceConnection {
  source: string
  target: string
  protocol: CommProtocol
  /** 'service' injects the target URL into the source; 'cors' allows the source origin on the target. */
  kind?: ConnectorKind
  /** Override the env var the target URL is published under (service connectors). */
  expose_as?: string | null
  /** Explicit allowed origin for a CORS connector (defaults to the live preview URL when blank). */
  cors_origin?: string | null
  /** Path appended to the backend base URL given to the frontend. Blank = just the base URL. */
  api_path?: string | null
}
