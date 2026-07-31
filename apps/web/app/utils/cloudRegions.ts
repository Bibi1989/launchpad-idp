export type CloudRegionOption = { value: string; label: string }

export const GCP_REGIONS: CloudRegionOption[] = [
  { value: 'us-central1', label: 'us-central1 (Iowa)' },
  { value: 'us-east1', label: 'us-east1 (South Carolina)' },
  { value: 'us-east4', label: 'us-east4 (Northern Virginia)' },
  { value: 'us-west1', label: 'us-west1 (Oregon)' },
  { value: 'us-west2', label: 'us-west2 (Los Angeles)' },
  { value: 'us-west3', label: 'us-west3 (Salt Lake City)' },
  { value: 'us-west4', label: 'us-west4 (Las Vegas)' },
  { value: 'northamerica-northeast1', label: 'northamerica-northeast1 (Montréal)' },
  { value: 'southamerica-east1', label: 'southamerica-east1 (São Paulo)' },
  { value: 'europe-west1', label: 'europe-west1 (Belgium)' },
  { value: 'europe-west2', label: 'europe-west2 (London)' },
  { value: 'europe-west3', label: 'europe-west3 (Frankfurt)' },
  { value: 'europe-west4', label: 'europe-west4 (Netherlands)' },
  { value: 'europe-north1', label: 'europe-north1 (Finland)' },
  { value: 'asia-east1', label: 'asia-east1 (Taiwan)' },
  { value: 'asia-northeast1', label: 'asia-northeast1 (Tokyo)' },
  { value: 'asia-southeast1', label: 'asia-southeast1 (Singapore)' },
  { value: 'australia-southeast1', label: 'australia-southeast1 (Sydney)' },
]

export const AWS_REGIONS: CloudRegionOption[] = [
  { value: 'us-east-1', label: 'us-east-1 (N. Virginia)' },
  { value: 'us-east-2', label: 'us-east-2 (Ohio)' },
  { value: 'us-west-1', label: 'us-west-1 (N. California)' },
  { value: 'us-west-2', label: 'us-west-2 (Oregon)' },
  { value: 'ca-central-1', label: 'ca-central-1 (Canada)' },
  { value: 'eu-west-1', label: 'eu-west-1 (Ireland)' },
  { value: 'eu-west-2', label: 'eu-west-2 (London)' },
  { value: 'eu-west-3', label: 'eu-west-3 (Paris)' },
  { value: 'eu-central-1', label: 'eu-central-1 (Frankfurt)' },
  { value: 'eu-north-1', label: 'eu-north-1 (Stockholm)' },
  { value: 'ap-southeast-1', label: 'ap-southeast-1 (Singapore)' },
  { value: 'ap-southeast-2', label: 'ap-southeast-2 (Sydney)' },
  { value: 'ap-northeast-1', label: 'ap-northeast-1 (Tokyo)' },
  { value: 'ap-south-1', label: 'ap-south-1 (Mumbai)' },
  { value: 'sa-east-1', label: 'sa-east-1 (São Paulo)' },
]

export const AZURE_LOCATIONS: CloudRegionOption[] = [
  { value: 'eastus', label: 'East US' },
  { value: 'eastus2', label: 'East US 2' },
  { value: 'westus', label: 'West US' },
  { value: 'westus2', label: 'West US 2' },
  { value: 'westus3', label: 'West US 3' },
  { value: 'centralus', label: 'Central US' },
  { value: 'northcentralus', label: 'North Central US' },
  { value: 'southcentralus', label: 'South Central US' },
  { value: 'canadacentral', label: 'Canada Central' },
  { value: 'westeurope', label: 'West Europe' },
  { value: 'northeurope', label: 'North Europe' },
  { value: 'uksouth', label: 'UK South' },
  { value: 'ukwest', label: 'UK West' },
  { value: 'southeastasia', label: 'Southeast Asia' },
  { value: 'australiaeast', label: 'Australia East' },
  { value: 'japaneast', label: 'Japan East' },
]

/** Compute / node pool instance sizes by cloud. */
export const GCP_MACHINE_TYPES: CloudRegionOption[] = [
  { value: 'e2-medium', label: 'e2-medium (2 vCPU, 4 GB)' },
  { value: 'e2-standard-2', label: 'e2-standard-2 (2 vCPU, 8 GB)' },
  { value: 'e2-standard-4', label: 'e2-standard-4 (4 vCPU, 16 GB)' },
  { value: 'e2-standard-8', label: 'e2-standard-8 (8 vCPU, 32 GB)' },
  { value: 'n2-standard-2', label: 'n2-standard-2 (2 vCPU, 8 GB)' },
  { value: 'n2-standard-4', label: 'n2-standard-4 (4 vCPU, 16 GB)' },
]

export const AWS_INSTANCE_TYPES: CloudRegionOption[] = [
  { value: 't3.micro', label: 't3.micro (2 vCPU, 1 GB)' },
  { value: 't3.small', label: 't3.small (2 vCPU, 2 GB)' },
  { value: 't3.medium', label: 't3.medium (2 vCPU, 4 GB)' },
  { value: 't3.large', label: 't3.large (2 vCPU, 8 GB)' },
  { value: 'm5.large', label: 'm5.large (2 vCPU, 8 GB)' },
  { value: 'm5.xlarge', label: 'm5.xlarge (4 vCPU, 16 GB)' },
]

export const AZURE_VM_SIZES: CloudRegionOption[] = [
  { value: 'Standard_B2s', label: 'Standard_B2s (2 vCPU, 4 GB)' },
  { value: 'Standard_D2_v2', label: 'Standard_D2_v2 (2 vCPU, 7 GB)' },
  { value: 'Standard_D2s_v3', label: 'Standard_D2s_v3 (2 vCPU, 8 GB)' },
  { value: 'Standard_D4s_v3', label: 'Standard_D4s_v3 (4 vCPU, 16 GB)' },
  { value: 'Standard_E2s_v3', label: 'Standard_E2s_v3 (2 vCPU, 16 GB)' },
]

/** Union used by the structured IaC editor when provider is unknown. */
export const INSTANCE_SIZE_OPTIONS: CloudRegionOption[] = [
  ...GCP_MACHINE_TYPES,
  ...AWS_INSTANCE_TYPES,
  ...AZURE_VM_SIZES,
]
