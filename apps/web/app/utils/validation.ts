import { z } from 'zod'

export const environmentCreateSchema = z
  .object({
    name: z
      .string()
      .trim()
      .toLowerCase()
      .min(3, 'Name must be at least 3 characters')
      .max(64, 'Name must be at most 64 characters')
      .regex(/^[a-z][a-z0-9-]*$/, 'Use lowercase letters, numbers, and hyphens'),
    git_branch: z
      .string()
      .trim()
      .min(1, 'Git branch is required')
      .max(256)
      .refine((value) => !value.includes(' ') && !value.includes('..'), {
        message: 'Invalid git branch name',
      }),
    git_repo_url: z
      .string()
      .trim()
      .min(8, 'Git repository URL is required')
      .max(512)
      .refine(
        (value) =>
          value.startsWith('https://') ||
          value.startsWith('http://') ||
          value.startsWith('git@') ||
          value.startsWith('ssh://'),
        { message: 'Use an http(s), git@, or ssh repository URL' },
      )
      .refine((value) => !/\s/.test(value), { message: 'URL must not contain spaces' }),
    ttl_unit: z.enum(['hours', 'minutes']).default('hours'),
    ttl_value: z.coerce.number().int().min(1),
    workspace_id: z.string().uuid().nullable().optional(),
  })
  .superRefine((data, ctx) => {
    const max = data.ttl_unit === 'minutes' ? 43_200 : 720
    if (data.ttl_value > max) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['ttl_value'],
        message:
          data.ttl_unit === 'minutes'
            ? 'TTL must be at most 43200 minutes (30 days)'
            : 'TTL must be at most 720 hours',
      })
    }
  })
  .transform((data) => {
    const base = {
      name: data.name,
      git_branch: data.git_branch,
      git_repo_url: data.git_repo_url,
      workspace_id: data.workspace_id,
    }
    if (data.ttl_unit === 'minutes') {
      return { ...base, ttl_minutes: data.ttl_value, ttl_hours: undefined }
    }
    return { ...base, ttl_hours: data.ttl_value, ttl_minutes: undefined }
  })

export type EnvironmentCreateInput = z.infer<typeof environmentCreateSchema>
