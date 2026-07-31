export type UserCloudCredentialsStatus = {
  has_gcp: boolean
  has_aws: boolean
  has_azure: boolean
  has_cloudflare: boolean
  gcp_label?: string | null
  aws_label?: string | null
  azure_label?: string | null
  cloudflare_label?: string | null
  updated_at?: string | null
}
