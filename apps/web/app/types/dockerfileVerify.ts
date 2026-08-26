// Types mirroring the advisory Dockerfile build+run+probe verification API
// (schemas/dockerfile_schema.py: DockerfileVerify*).

export type DockerfileVerifyStatus = 'verified' | 'warning' | 'skipped'

export interface DockerfileVerifyServiceSpec {
  name?: string
  path?: string
  dockerfile_path?: string
  listen_port?: number | null
}

export interface DockerfileVerifyResult {
  service: string
  status: DockerfileVerifyStatus
  used_repo_dockerfile: boolean
  generated_stack: string | null
  built: boolean
  ran: boolean
  probe_ok: boolean
  listen_port: number | null
  warning: string | null
  logs_tail: string | null
}

export interface DockerfileVerifyResponse {
  results: DockerfileVerifyResult[]
}
