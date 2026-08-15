import type { Environment, EnvironmentStatus, EnvStreamEvent } from '~/types/environment'

/**
 * Merge a Redis→SSE env event into a local Environment snapshot.
 * Only applies fields present on the event so partial status ticks stay safe.
 */
export function applyEnvStreamPatch(
  env: Environment,
  event: EnvStreamEvent,
): Environment {
  if (event.status) {
    env.status = event.status as EnvironmentStatus
  }
  if (event.commit_sha !== undefined && event.commit_sha !== null) {
    env.latest_commit_sha = event.commit_sha
  }
  if (event.preview_url !== undefined && event.preview_url !== null) {
    env.preview_url = event.preview_url
  }
  if (event.node_port !== undefined && event.node_port !== null) {
    env.node_port = event.node_port
  }
  if (event.preview_endpoints !== undefined && event.preview_endpoints !== null) {
    env.preview_endpoints = event.preview_endpoints
  }
  if (event.app_ready !== undefined && event.app_ready !== null) {
    env.app_ready = event.app_ready
  } else if (event.status === 'RUNNING' && event.preview_url) {
    env.app_ready = true
  } else if (event.status === 'FAILED' || event.status === 'PROVISIONING') {
    if (event.status === 'FAILED') env.app_ready = false
  }
  if (event.error_message !== undefined && event.error_message !== null) {
    env.error_message = event.error_message
  } else if (event.status === 'PROVISIONING') {
    env.error_message = null
  }
  if (event.failure_summary !== undefined && event.failure_summary !== null) {
    env.failure_summary = event.failure_summary
  } else if (event.status === 'PROVISIONING' || event.status === 'RUNNING') {
    env.failure_summary = null
  }
  return env
}

/** Build a Partial Environment patch for shared list state from an SSE event. */
export function envStreamToPatch(
  environmentId: string,
  event: EnvStreamEvent,
): Partial<Environment> & { id: string } {
  const patch: Partial<Environment> & { id: string } = { id: environmentId }
  if (event.status) patch.status = event.status as EnvironmentStatus
  if (event.commit_sha !== undefined) patch.latest_commit_sha = event.commit_sha
  if (event.preview_url !== undefined && event.preview_url !== null) {
    patch.preview_url = event.preview_url
  }
  if (event.node_port !== undefined && event.node_port !== null) {
    patch.node_port = event.node_port
  }
  if (event.preview_endpoints !== undefined && event.preview_endpoints !== null) {
    patch.preview_endpoints = event.preview_endpoints
  }
  if (event.app_ready !== undefined && event.app_ready !== null) {
    patch.app_ready = event.app_ready
  } else if (event.status === 'RUNNING' && event.preview_url) {
    patch.app_ready = true
  } else if (event.status === 'FAILED') {
    patch.app_ready = false
  }
  if (event.error_message !== undefined) {
    patch.error_message = event.error_message
  } else if (event.status === 'PROVISIONING') {
    patch.error_message = null
  }
  if (event.failure_summary !== undefined) {
    patch.failure_summary = event.failure_summary
  } else if (event.status === 'PROVISIONING' || event.status === 'RUNNING') {
    patch.failure_summary = null
  }
  return patch
}
