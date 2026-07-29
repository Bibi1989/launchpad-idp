import { z } from 'zod'

export const environmentCreateSchema = z.object({
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
  ttl_hours: z.coerce.number().int().min(1).max(720),
  workspace_id: z.string().uuid().nullable().optional(),
})

export type EnvironmentCreateInput = z.infer<typeof environmentCreateSchema>
