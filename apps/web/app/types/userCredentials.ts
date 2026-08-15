export type UserCloudCredentialsStatus = {
  has_gcp: boolean
  has_aws: boolean
  has_azure: boolean
  has_cloudflare: boolean
  has_gcp_sa?: boolean
  has_gcp_oauth?: boolean
  gcp_label?: string | null
  aws_label?: string | null
  azure_label?: string | null
  cloudflare_label?: string | null
  gcp_project_id?: string | null
  gcp_region?: string | null
  aws_region?: string | null
  azure_location?: string | null
  updated_at?: string | null
  /** True when vault ciphertext could not be decrypted (cleared on status GET). */
  vault_unreadable?: boolean
}
