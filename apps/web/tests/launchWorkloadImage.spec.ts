import { describe, expect, it } from 'vitest'
import {
  launchRequiresWorkloadImage,
  launchShowsWorkloadImageInput,
} from '~/utils/launchWorkloadImage'

describe('launchWorkloadImage', () => {
  it('does not require image when launching from a workspace', () => {
    expect(
      launchRequiresWorkloadImage({
        usesWorkspaceSource: true,
        buildsFromRepo: false,
        workspaceHasManifests: false,
        deployMode: 'attach',
      }),
    ).toBe(false)
    expect(
      launchShowsWorkloadImageInput({
        usesWorkspaceSource: true,
        buildsFromRepo: false,
        workspaceHasManifests: false,
        deployMode: 'attach',
      }),
    ).toBe(false)
  })

  it('does not require image for compose workspace plans', () => {
    expect(
      launchRequiresWorkloadImage({
        usesWorkspaceSource: true,
        buildsFromRepo: false,
        workspaceHasManifests: false,
        deployMode: 'compose',
      }),
    ).toBe(false)
  })

  it('requires image for local image-only launches', () => {
    expect(
      launchRequiresWorkloadImage({
        usesWorkspaceSource: false,
        buildsFromRepo: false,
        workspaceHasManifests: false,
        deployMode: null,
      }),
    ).toBe(true)
  })

  it('skips image when building from repo', () => {
    expect(
      launchRequiresWorkloadImage({
        usesWorkspaceSource: false,
        buildsFromRepo: true,
        workspaceHasManifests: false,
        deployMode: null,
      }),
    ).toBe(false)
  })
})
