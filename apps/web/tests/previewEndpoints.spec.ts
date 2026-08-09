import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Environment } from '~/types/environment'
import {
  resolvePreviewEndpoints,
  secondaryPreviewEndpoints,
} from '~/utils/previewEndpoints'
import { localizePreviewUrl, resolvePreviewUrl } from '~/utils/previewUrl'

function env(partial: Partial<Environment>): Environment {
  return {
    id: 'e8f9cf54-60c2-4556-8e45-2b654ea4e976',
    owner_id: 'o1',
    workspace_id: null,
    name: 'demo',
    git_branch: 'main',
    git_repo_url: 'https://example.com/r.git',
    latest_commit_sha: null,
    status: 'RUNNING',
    namespace_name: 'ns',
    preview_url: null,
    template_id: null,
    provider: 'local',
    deploy_mode: 'preview',
    ttl_expires_at: new Date().toISOString(),
    cost_estimate_hourly: '0',
    cost_accrued: '0',
    time_remaining_seconds: 0,
    error_message: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...partial,
  }
}

describe('localizePreviewUrl', () => {
  it('uses localhost ports when the viewer is local', () => {
    expect(
      localizePreviewUrl({
        url: 'https://pike-aspects-tub-baltimore.trycloudflare.com',
        port: 8090,
        provider: 'local',
        viewerHost: 'localhost',
      }),
    ).toBe('http://localhost:8090')
  })

  it('keeps public tunnel urls for remote viewers', () => {
    expect(
      localizePreviewUrl({
        url: 'https://pike-aspects-tub-baltimore.trycloudflare.com',
        port: 8090,
        provider: 'local',
        deployMode: 'attach',
        viewerHost: 'preview.example.com',
      }),
    ).toBe('https://pike-aspects-tub-baltimore.trycloudflare.com')
  })

  it('strips bogus ports from trycloudflare hosts for remote viewers', () => {
    expect(
      localizePreviewUrl({
        url: 'http://bluetooth-deck-wanting-katie.trycloudflare.com:8083',
        port: 8083,
        provider: 'local',
        viewerHost: 'preview.example.com',
      }),
    ).toBe('https://bluetooth-deck-wanting-katie.trycloudflare.com')
  })

  it('repairs apex:port into workspace ingress for remote k8s viewers', () => {
    expect(
      localizePreviewUrl({
        url: 'http://launchpad-idp.online:2001',
        port: 2001,
        provider: 'local',
        deployMode: 'preview',
        environmentId: 'e8f9cf54-60c2-4556-8e45-2b654ea4e976',
        viewerHost: 'launchpad-idp.online',
      }),
    ).toBe('https://ws-e8f9cf54-60c2-4556-8e45-2b654ea4e976.launchpad-idp.online')
  })

  it('does not invent ws-* hosts for attach/compose on remote viewers', () => {
    expect(
      localizePreviewUrl({
        url: 'http://127.0.0.1:8090',
        port: 8090,
        provider: 'local',
        deployMode: 'attach',
        environmentId: 'e8f9cf54-60c2-4556-8e45-2b654ea4e976',
        viewerHost: 'launchpad-idp.online',
      }),
    ).toBe('http://127.0.0.1:8090')
  })

  it('keeps existing workspace ingress urls', () => {
    expect(
      localizePreviewUrl({
        url: 'https://ws-e8f9cf54-60c2-4556-8e45-2b654ea4e976.launchpad-idp.online',
        port: 2001,
        provider: 'local',
        deployMode: 'preview',
        environmentId: 'e8f9cf54-60c2-4556-8e45-2b654ea4e976',
        viewerHost: 'launchpad-idp.online',
      }),
    ).toBe('https://ws-e8f9cf54-60c2-4556-8e45-2b654ea4e976.launchpad-idp.online')
  })
})

describe('previewEndpoints', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('rewrites stored tunnel endpoints to localhost when viewing locally', () => {
    vi.stubGlobal('window', {
      location: { hostname: 'localhost', protocol: 'http:' },
    })
    const e = env({
      preview_url: 'https://pike-aspects-tub-baltimore.trycloudflare.com',
      node_port: 8090,
      preview_endpoints: [
        {
          name: 'api-server',
          app_kind: 'backend',
          url: 'http://bluetooth-deck-wanting-katie.trycloudflare.com:8083',
          port: 8083,
        },
        {
          name: 'web-ui',
          app_kind: 'frontend',
          url: 'https://pike-aspects-tub-baltimore.trycloudflare.com',
          port: 8090,
        },
      ],
    })
    const endpoints = resolvePreviewEndpoints(e)
    expect(endpoints.map((x) => x.url)).toEqual([
      'http://localhost:8083',
      'http://localhost:8090',
    ])
    expect(resolvePreviewUrl(e)).toBe('http://localhost:8090')
    expect(secondaryPreviewEndpoints(e).map((x) => x.name)).toEqual(['api-server'])
  })

  it('repairs prod apex:port preview urls to workspace ingress for k8s', () => {
    vi.stubGlobal('window', {
      location: { hostname: 'launchpad-idp.online', protocol: 'https:' },
    })
    const e = env({
      deploy_mode: 'preview',
      preview_url: 'http://launchpad-idp.online:2001',
      node_port: 2001,
    })
    expect(resolvePreviewUrl(e)).toBe(
      'https://ws-e8f9cf54-60c2-4556-8e45-2b654ea4e976.launchpad-idp.online',
    )
  })

  it('keeps trycloudflare for remote attach viewers', () => {
    vi.stubGlobal('window', {
      location: { hostname: 'launchpad-idp.online', protocol: 'https:' },
    })
    const e = env({
      deploy_mode: 'attach',
      preview_url: 'https://pike-aspects-tub-baltimore.trycloudflare.com',
      node_port: 8090,
    })
    expect(resolvePreviewUrl(e)).toBe(
      'https://pike-aspects-tub-baltimore.trycloudflare.com',
    )
  })

  it('falls back to single preview_url', () => {
    vi.stubGlobal('window', {
      location: { hostname: '127.0.0.1', protocol: 'http:' },
    })
    const e = env({ preview_url: 'http://127.0.0.1:3000', node_port: 3000 })
    expect(resolvePreviewEndpoints(e)).toEqual([
      {
        name: 'app',
        app_kind: 'frontend',
        url: 'http://127.0.0.1:3000',
        port: 3000,
        exposed: true,
      },
    ])
    expect(secondaryPreviewEndpoints(e)).toEqual([])
  })
})
