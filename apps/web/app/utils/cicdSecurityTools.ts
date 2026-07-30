import type { CicdPlatform } from '~/types/provisioning'

/** Pinned SAST scanner selection (image ref or CodeQL action set). */
export type SastToolId =
  | 'codeql-v3.28.10'
  | 'semgrep-1.97.0'
  | 'semgrep-1.96.0'
  | 'semgrep-1.95.0'

/** Pinned container CVE scanner (GitHub Action or OCI image). */
export type ContainerScanToolId =
  | 'trivy-action-v0.30.0'
  | 'trivy-0.58.1'
  | 'trivy-0.57.2'
  | 'trivy-0.56.2'

export interface SastToolOption {
  id: SastToolId
  label: string
  hint: string
  platforms: CicdPlatform[]
  /** OCI image for Semgrep (GitHub docker run + GitLab job image). */
  image?: string
  codeqlInit?: string
  codeqlAnalyze?: string
}

export interface ContainerScanToolOption {
  id: ContainerScanToolId
  label: string
  hint: string
  platforms: CicdPlatform[]
  /** GitHub Actions `uses:` ref (aquasecurity/trivy-action@commit). */
  githubAction?: string
  /** OCI image for `docker run` / GitLab `image:`. */
  image?: string
}

export const SAST_TOOL_OPTIONS: SastToolOption[] = [
  {
    id: 'codeql-v3.28.10',
    label: 'GitHub CodeQL v3.28.10',
    hint: 'github/codeql-action (SHA-pinned init + analyze)',
    platforms: ['github'],
    codeqlInit: 'github/codeql-action/init@b56ba49b26e50535fa1e7f7db0f4f7b4bf65d80d',
    codeqlAnalyze: 'github/codeql-action/analyze@b56ba49b26e50535fa1e7f7db0f4f7b4bf65d80d',
  },
  {
    id: 'semgrep-1.97.0',
    label: 'Semgrep 1.97.0',
    hint: 'returntocorp/semgrep:1.97.0',
    platforms: ['github', 'gitlab'],
    image: 'returntocorp/semgrep:1.97.0',
  },
  {
    id: 'semgrep-1.96.0',
    label: 'Semgrep 1.96.0',
    hint: 'returntocorp/semgrep:1.96.0',
    platforms: ['github', 'gitlab'],
    image: 'returntocorp/semgrep:1.96.0',
  },
  {
    id: 'semgrep-1.95.0',
    label: 'Semgrep 1.95.0',
    hint: 'returntocorp/semgrep:1.95.0',
    platforms: ['github', 'gitlab'],
    image: 'returntocorp/semgrep:1.95.0',
  },
]

export const CONTAINER_SCAN_TOOL_OPTIONS: ContainerScanToolOption[] = [
  {
    id: 'trivy-action-v0.30.0',
    label: 'Trivy GitHub Action v0.30.0',
    hint: 'aquasecurity/trivy-action@6c175e9c… (SHA-pinned)',
    platforms: ['github'],
    githubAction: 'aquasecurity/trivy-action@6c175e9c4083a92bbca2f9724c8a5e33bc2d97a5',
  },
  {
    id: 'trivy-0.58.1',
    label: 'aquasec/trivy:0.58.1',
    hint: 'Trivy CLI container image',
    platforms: ['github', 'gitlab'],
    image: 'aquasec/trivy:0.58.1',
  },
  {
    id: 'trivy-0.57.2',
    label: 'aquasec/trivy:0.57.2',
    hint: 'Trivy CLI container image',
    platforms: ['github', 'gitlab'],
    image: 'aquasec/trivy:0.57.2',
  },
  {
    id: 'trivy-0.56.2',
    label: 'aquasec/trivy:0.56.2',
    hint: 'Trivy CLI container image',
    platforms: ['github', 'gitlab'],
    image: 'aquasec/trivy:0.56.2',
  },
]

export function sastToolsForPlatform(platform: CicdPlatform): SastToolOption[] {
  return SAST_TOOL_OPTIONS.filter((opt) => opt.platforms.includes(platform))
}

export function containerScanToolsForPlatform(platform: CicdPlatform): ContainerScanToolOption[] {
  return CONTAINER_SCAN_TOOL_OPTIONS.filter((opt) => opt.platforms.includes(platform))
}

export function defaultSastToolForPlatform(platform: CicdPlatform): SastToolId {
  return platform === 'github' ? 'codeql-v3.28.10' : 'semgrep-1.97.0'
}

export function defaultContainerScanToolForPlatform(platform: CicdPlatform): ContainerScanToolId {
  return platform === 'github' ? 'trivy-action-v0.30.0' : 'trivy-0.58.1'
}

export function getSastToolOption(id: SastToolId): SastToolOption {
  return SAST_TOOL_OPTIONS.find((opt) => opt.id === id) ?? SAST_TOOL_OPTIONS[1]!
}

export function getContainerScanToolOption(id: ContainerScanToolId): ContainerScanToolOption {
  return CONTAINER_SCAN_TOOL_OPTIONS.find((opt) => opt.id === id) ?? CONTAINER_SCAN_TOOL_OPTIONS[0]!
}

export function normalizeSastToolId(
  id: string | undefined,
  platform: CicdPlatform,
): SastToolId {
  const match = SAST_TOOL_OPTIONS.find((opt) => opt.id === id && opt.platforms.includes(platform))
  return match?.id ?? defaultSastToolForPlatform(platform)
}

export function normalizeContainerScanToolId(
  id: string | undefined,
  platform: CicdPlatform,
): ContainerScanToolId {
  const match = CONTAINER_SCAN_TOOL_OPTIONS.find((opt) => opt.id === id && opt.platforms.includes(platform))
  return match?.id ?? defaultContainerScanToolForPlatform(platform)
}
