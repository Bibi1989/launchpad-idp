import {
  dockerfileBuildEnqueueResponseSchema,
  dockerfileBuildJobResponseSchema,
  dockerfilePushResponseSchema,
  dockerfileReviewResponseSchema,
  dockerfileScaffoldResponseSchema,
  dockerfileScanResponseSchema,
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
    loading.value = true
    error.value = null
    try {
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
      if (parsed.dockerfiles.length > 0) {
        const first = parsed.dockerfiles[0]
        selectedPath.value = first.path
        editorContent.value = first.content
      } else {
        selectedPath.value = null
        editorContent.value = ''
      }
      return parsed
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Dockerfile scan failed'
      throw err
    } finally {
      loading.value = false
    }
  }

  async function scaffold(input: {
    installation_id?: number | null
    full_name?: string | null
    stack?: ProjectStack | null
    ref?: string | null
    app_name?: string | null
    listen_port?: number
  }): Promise<DockerfileScaffoldResponse> {
    loading.value = true
    error.value = null
    try {
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
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Dockerfile scaffold failed'
      throw err
    } finally {
      loading.value = false
    }
  }

  async function review(input: {
    dockerfile_content: string
    stack?: ProjectStack | null
    source_path?: string | null
  }): Promise<DockerfileReviewResponse> {
    loading.value = true
    error.value = null
    try {
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
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Dockerfile review failed'
      throw err
    } finally {
      loading.value = false
    }
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
    loading.value = true
    error.value = null
    try {
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
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Dockerfile push failed'
      throw err
    } finally {
      loading.value = false
    }
  }

  async function enqueueBuild(
    payload: DockerfileBuildPayload,
  ): Promise<DockerfileBuildEnqueueResponse> {
    loading.value = true
    error.value = null
    try {
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
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Build enqueue failed'
      throw err
    } finally {
      loading.value = false
    }
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
    enqueueBuild,
    getBuildJob,
    pollBuildJob,
    selectDetected,
    clearReport,
  }
}

export type { RegistryProvider }
