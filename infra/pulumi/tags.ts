export interface GovernanceTags {
  environmentId: string
  owner: string
  createdBy: string
  ttlExpiration: string
}

/** Required cost-governance labels for every provisioned resource. */
export function governanceLabels(tags: GovernanceTags): Record<string, string> {
  return {
    EnvironmentId: tags.environmentId,
    Owner: tags.owner,
    CreatedBy: tags.createdBy,
    TTL_Expiration: tags.ttlExpiration,
    'launchpad.io/environment-id': tags.environmentId,
    'launchpad.io/managed-by': 'launchpad-idp',
  }
}

export function assertGovernanceTags(tags: Partial<GovernanceTags>): asserts tags is GovernanceTags {
  const required: Array<keyof GovernanceTags> = [
    'environmentId',
    'owner',
    'createdBy',
    'ttlExpiration',
  ]
  for (const key of required) {
    if (!tags[key] || tags[key].trim().length === 0) {
      throw new Error(`Missing required governance tag: ${key}`)
    }
  }
}
