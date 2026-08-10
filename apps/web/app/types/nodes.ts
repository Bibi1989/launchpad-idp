// Types mirroring the hybrid agent-node API (app/schemas/nodes.py).

export type NodeStatus = 'PENDING' | 'ONLINE' | 'OFFLINE' | 'REVOKED'

export interface ContainerSummary {
  id: string
  name: string
  image: string
  status: string
  ports: string[]
}

export interface NodeRead {
  id: string
  name: string
  status: NodeStatus
  online: boolean
  labels: Record<string, string>
  hostname: string | null
  platform: string | null
  agent_version: string | null
  cpu_cores: number | null
  mem_total_mb: number | null
  last_heartbeat_at: string | null
  cpu_percent: number | null
  mem_percent: number | null
  disk_percent: number | null
  docker_status: string | null
  containers: ContainerSummary[]
  created_at: string
}

export interface NodeEnrollPayload {
  name: string
  labels?: Record<string, string>
}

export interface NodeInstallInstructions {
  node_id: string
  name: string
  token: string
  expires_at: string
  control_plane_url: string
  agent_ws_url: string
  install_command: string
}

export type NodeCommandAction =
  | 'pull_image'
  | 'run_container'
  | 'stop_container'
  | 'restart_container'
  | 'collect_logs'
  | 'list_containers'

export interface PortMapping {
  container_port: number
  host_port: number
  protocol?: 'tcp' | 'udp'
}

export interface RunContainerSpec {
  image: string
  name: string
  ports?: PortMapping[]
  env?: Record<string, string>
  cpu_limit?: number | null
  memory_mb?: number | null
  restart_policy?: string
  command?: string | null
  pull?: boolean
}

export interface NodeCommandRequest {
  action: NodeCommandAction
  run?: RunContainerSpec | null
  pull?: { image: string } | null
  ref?: { container: string } | null
  logs?: { container: string; tail?: number } | null
}

export interface NodeCommandResult {
  command_id: string
  action: NodeCommandAction
  ok: boolean
  detail: string
  data: Record<string, unknown>
}
