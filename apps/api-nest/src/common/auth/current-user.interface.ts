/**
 * The authenticated user, decoded from the JWT.
 *
 * Field names map directly to the claims the FastAPI app puts in the token
 * (sub = user id, plus email and optional org context).
 */
export interface CurrentUser {
  userId: string;
  email: string;
  orgId?: string;
  orgRole?: string;
}
