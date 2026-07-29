import { z } from 'zod'

export const loginSchema = z.object({
  email: z.string().trim().email('Valid email required'),
  password: z.string().min(1, 'Password is required').max(128),
})

export const registerSchema = z.object({
  email: z.string().trim().email('Valid email required'),
  password: z.string().min(8, 'Password must be at least 8 characters').max(128),
  display_name: z.string().trim().min(1, 'Display name is required').max(128),
})

export type LoginInput = z.infer<typeof loginSchema>
export type RegisterInput = z.infer<typeof registerSchema>
