import {
  dockerfileBuildEnqueueResponseSchema,
  dockerfileBuildJobResponseSchema,
  dockerfilePushResponseSchema,
  dockerfileReviewResponseSchema,
  dockerfileScaffoldResponseSchema,
  dockerfileScanResponseSchema,
  repoPushBundleResponseSchema,
  type DockerfileBuildEnqueueResponse,
  type DockerfileBuildJobResponse,
  type DockerfileBuildPayload,
  type DockerfilePushResponse,
  type DockerfileReviewResponse,
  type DockerfileScaffoldResponse,
  type DockerfileScanResponse,
  type DockerfileSecurityReport,
  type ProjectStack,
  type RegistryProvider,
  type RepoPushBundleResponse,
} from '~/types/dockerfileSchema'

export function useDockerfiles() {
  const { apiFetch } = useApi()

  const loading = useState<boolean>('dockerfile-loading', () => false)
  const error = useState<string | null>('dockerfile-error', () => null)
  const scanResult = useState<DockerfileScanResponse | null>('dockerfile-scan', () => null)
  const selectedPath = useState<string | null>('dockerfile-selected-path', () => null)
  const editorContent = useState<string>('dockerfile-editor', () => '')
  const report = useState<DockerfileSecurityReport | null>('dockerfile-report', () => null)
  const buildJob = useState<DockerfileBuildJobResponse | null>('dockerfile-build-job', () => null)

  async function scanRepo(input: {
    installation_id: number
    full_name: string
    ref?: string | null
  }): Promise<DockerfileScanResponse> {
    return runRequest({ loading, error }, async () => {
      const raw = await apiFetch<unknown>('/dockerfiles/scan', {
        method: 'POST',
        body: JSON.stringify({
          installation_id: input.installation_id,
          full_name: input.full_name,
          ref: input.ref ?? null,
        }),
        timeoutMs: 60_000,
      })
      const parsed = dockerfileScanResponseSchema.parse(raw)
      scanResult.value = parsed
      const first = parsed.dockerfiles[0]
      if (first) {
        selectedPath.value = first.path
        editorContent.value = first.content
      } else {
        selectedPath.value = null
        editorContent.value = ''
      }
      return parsed
    }, 'Dockerfile scan failed')
  }

  async function scaffold(input: {
    installation_id?: number | null
    full_name?: string | null
    stack?: ProjectStack | null
    ref?: string | null
    app_name?: string | null
    listen_port?: number
  }): Promise<DockerfileScaffoldResponse> {
    return runRequest({ loading, error }, async () => {
      const raw = await apiFetch<unknown>('/dockerfiles/scaffold', {
        method: 'POST',
        body: JSON.stringify({
          installation_id: input.installation_id ?? null,
          full_name: input.full_name ?? null,
          stack: input.stack ?? null,
          ref: input.ref ?? null,
          app_name: input.app_name ?? null,
          listen_port: input.listen_port ?? 8080,
        }),
        timeoutMs: 30_000,
      })
      const parsed = dockerfileScaffoldResponseSchema.parse(raw)
      selectedPath.value = parsed.path
      editorContent.value = parsed.content
      return parsed
    }, 'Dockerfile scaffold failed')
  }

  async function review(input: {
    dockerfile_content: string
    stack?: ProjectStack | null
    source_path?: string | null
  }): Promise<DockerfileReviewResponse> {
    return runRequest({ loading, error }, async () => {
      const raw = await apiFetch<unknown>('/dockerfiles/review', {
        method: 'POST',
        body: JSON.stringify({
          dockerfile_content: input.dockerfile_content,
          stack: input.stack ?? null,
          source_path: input.source_path ?? null,
        }),
        timeoutMs: 90_000,
      })
      const parsed = dockerfileReviewResponseSchema.parse(raw)
      report.value = parsed.report
      return parsed
    }, 'Dockerfile review failed')
  }

  async function applyImprovedDockerfile() {
    if (!report.value?.improvedDockerfile) return
    editorContent.value = report.value.improvedDockerfile
    selectedPath.value = selectedPath.value ?? 'dockers/Dockerfile'
  }

  async function pushToGitHub(input: {
    installation_id: number
    full_name: string
    dockerfile_content: string
    path?: string
    commit_message?: string
    branch?: string | null
  }): Promise<DockerfilePushResponse> {
    return runRequest({ loading, error }, async () => {
      const raw = await apiFetch<unknown>('/dockerfiles/push', {
        method: 'POST',
        body: JSON.stringify({
          installation_id: input.installation_id,
          full_name: input.full_name,
          dockerfile_content: input.dockerfile_content,
          path: input.path ?? 'dockers/Dockerfile',
          commit_message:
            input.commit_message ?? 'chore: add Launchpad-hardened Dockerfile under dockers/',
          branch: input.branch ?? null,
        }),
        timeoutMs: 60_000,
      })
      return dockerfilePushResponseSchema.parse(raw)
    }, 'Dockerfile push failed')
  }

  async function pushBundle(input: {
    installation_id: number
    full_name: string
    files: Array<{ path: string; content: string }>
    commit_message?: string
    branch?: string | null
  }): Promise<RepoPushBundleResponse> {
    return runRequest({ loading, error }, async () => {
      const raw = await apiFetch<unknown>('/dockerfiles/push-bundle', {
        method: 'POST',
        body: JSON.stringify({
          installation_id: input.installation_id,
          full_name: input.full_name,
          files: input.files,
          commit_message: input.commit_message ?? 'chore: add Launchpad infra scaffold',
          branch: input.branch ?? null,
        }),
        timeoutMs: 90_000,
      })
      return repoPushBundleResponseSchema.parse(raw)
    }, 'Scaffold push failed')
  }

  async function enqueueBuild(
    payload: DockerfileBuildPayload,
  ): Promise<DockerfileBuildEnqueueResponse> {
    return runRequest({ loading, error }, async () => {
      const raw = await apiFetch<unknown>('/dockerfiles/build', {
        method: 'POST',
        body: JSON.stringify(payload),
        timeoutMs: 30_000,
      })
      const parsed = dockerfileBuildEnqueueResponseSchema.parse(raw)
      buildJob.value = {
        job_id: parsed.job_id,
        status: parsed.status,
        image_refs: [],
        logs: [],
        error: null,
        created_at: null,
        updated_at: null,
      }
      return parsed
    }, 'Build enqueue failed')
  }

  async function getBuildJob(jobId: string): Promise<DockerfileBuildJobResponse> {
    const raw = await apiFetch<unknown>(`/dockerfiles/build/${jobId}`)
    const parsed = dockerfileBuildJobResponseSchema.parse(raw)
    buildJob.value = parsed
    return parsed
  }

  async function pollBuildJob(
    jobId: string,
    opts: { intervalMs?: number; maxAttempts?: number } = {},
  ): Promise<DockerfileBuildJobResponse> {
    const intervalMs = opts.intervalMs ?? 2500
    const maxAttempts = opts.maxAttempts ?? 120
    let last: DockerfileBuildJobResponse | null = null
    for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
      last = await getBuildJob(jobId)
      if (last.status === 'succeeded' || last.status === 'failed') {
        return last
      }
      await new Promise((resolve) => setTimeout(resolve, intervalMs))
    }
    if (!last) {
      throw new Error('Build job polling failed')
    }
    return last
  }

  function selectDetected(path: string) {
    const match = scanResult.value?.dockerfiles.find((d) => d.path === path)
    if (!match) return
    selectedPath.value = match.path
    editorContent.value = match.content
  }

  function clearReport() {
    report.value = null
  }

  return {
    loading,
    error,
    scanResult,
    selectedPath,
    editorContent,
    report,
    buildJob,
    scanRepo,
    scaffold,
    review,
    applyImprovedDockerfile,
    pushToGitHub,
    pushBundle,
    enqueueBuild,
    getBuildJob,
    pollBuildJob,
    selectDetected,
    clearReport,
  }
}

export type { RegistryProvider }
