/**
 * Catalog shapes for the multi-cloud provider engine. These mirror, field for field,
 * the JSON the FastAPI app returns (app/providers/registry.py + tools.py +
 * provider_services.py), so the frontend cannot tell the two backends apart.
 */

export type RuntimeTarget = 'vm' | 'docker_host' | 'kubernetes' | 'paas';

export interface CredentialField {
  name: string;
  label: string;
  secret: boolean;
  required: boolean;
  help: string | null;
  placeholder: string | null;
}

export interface RegionOption {
  value: string;
  label: string;
}

export interface ComputeTier {
  id: string;
  label: string;
  vcpus: number | null;
  memory_mb: number | null;
  monthly_usd: number | null;
}

export interface CloudService {
  id: string;
  label: string;
  description: string;
}

export interface CloudServiceGroup {
  runtime: string;
  label: string;
  services: CloudService[];
}

export interface CloudProviderCatalogEntry {
  id: string;
  label: string;
  docs_url: string | null;
  runtime_targets: RuntimeTarget[];
  credential_fields: CredentialField[];
  regions: RegionOption[];
  tiers: ComputeTier[];
  services?: CloudServiceGroup[];
  source?: string;
  icon?: string | null;
  description?: string;
  parent_cloud?: string | null;
  service?: string;
  owner?: 'user' | 'organization';
  visibility?: 'private' | 'public';
  can_edit?: boolean;
}

export interface ProvisioningTool {
  id: string;
  label: string;
  category: 'iac' | 'config';
  description: string;
  supported_clouds: string[];
  docs_url: string | null;
  implemented: boolean;
  default: boolean;
}

// Small builders keep the data tables below readable and free of repetition.
export function credential(
  name: string,
  label: string,
  options: Partial<Omit<CredentialField, 'name' | 'label'>> = {},
): CredentialField {
  return {
    name,
    label,
    secret: options.secret ?? true,
    required: options.required ?? true,
    help: options.help ?? null,
    placeholder: options.placeholder ?? null,
  };
}

export function region(value: string, label: string): RegionOption {
  return { value, label };
}

export function tier(
  id: string,
  label: string,
  options: Partial<Omit<ComputeTier, 'id' | 'label'>> = {},
): ComputeTier {
  return {
    id,
    label,
    vcpus: options.vcpus ?? null,
    memory_mb: options.memory_mb ?? null,
    monthly_usd: options.monthly_usd ?? null,
  };
}
