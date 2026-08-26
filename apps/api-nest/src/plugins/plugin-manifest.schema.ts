/**
 * TypeScript mirror of FastAPI ``app.plugins.manifest.PluginManifest``.
 * Accepts snake_case and camelCase the same way the Python model does.
 */

import type { CloudProviderCatalogEntry } from '../cloud-providers/cloud-providers.types';

export const RUNNER_TYPES = [
  'terraform',
  'opentofu',
  'pulumi',
  'ansible',
  'node',
  'python',
  'binary',
  'cli',
  'docker',
  'script',
] as const;

export type RunnerType = (typeof RUNNER_TYPES)[number];

const RUNNER_TYPE_SET = new Set<string>(RUNNER_TYPES);

const JSON_SCHEMA_TYPES = new Set([
  'object',
  'array',
  'string',
  'number',
  'integer',
  'boolean',
  'null',
]);

const SEMVER = /^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/;

export interface FieldError {
  loc: string;
  msg: string;
}

export interface PluginManifestValidated {
  id: string;
  label: string;
  version: string;
  category: string | null;
  description: string | null;
  icon: string | null;
  runner: Record<string, unknown>;
  capabilities: string[] | Record<string, unknown>;
  credentials_schema: Record<string, unknown>;
  deployment_config_schema: Record<string, unknown>;
  parent_cloud?: string | null;
  homepage?: string | null;
  license?: string | null;
  author?: string | null;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function str(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

export function slugify(value: string): string {
  const slug = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return slug || 'plugin';
}

/** Draft-7 structural check (same intent as Python jsonschema.Draft7Validator.check_schema). */
export function validateJsonSchemaDocument(value: unknown, loc: string): FieldError[] {
  if (value === undefined || value === null) return [];
  const rec = asRecord(value);
  if (!rec) {
    return [{ loc, msg: 'not a valid JSON Schema: must be an object' }];
  }
  if (Object.keys(rec).length === 0) return [];

  const errors: FieldError[] = [];
  const typeVal = rec.type;
  if (typeof typeVal === 'string' && !JSON_SCHEMA_TYPES.has(typeVal)) {
    errors.push({ loc, msg: `not a valid JSON Schema: '${typeVal}' is not a valid type` });
  }
  if (Array.isArray(typeVal)) {
    for (const item of typeVal) {
      if (typeof item !== 'string' || !JSON_SCHEMA_TYPES.has(item)) {
        errors.push({ loc, msg: `not a valid JSON Schema: '${String(item)}' is not a valid type` });
        break;
      }
    }
  }
  if (rec.properties !== undefined) {
    const props = asRecord(rec.properties);
    if (!props) {
      errors.push({
        loc: `${loc}.properties`,
        msg: 'not a valid JSON Schema: properties must be an object',
      });
    } else {
      for (const [key, nested] of Object.entries(props)) {
        errors.push(...validateJsonSchemaDocument(nested, `${loc}.properties.${key}`));
      }
    }
  }
  if (rec.items !== undefined) {
    if (Array.isArray(rec.items)) {
      rec.items.forEach((item, index) => {
        errors.push(...validateJsonSchemaDocument(item, `${loc}.items.${index}`));
      });
    } else {
      errors.push(...validateJsonSchemaDocument(rec.items, `${loc}.items`));
    }
  }
  if (rec.required !== undefined && !Array.isArray(rec.required)) {
    errors.push({ loc: `${loc}.required`, msg: 'not a valid JSON Schema: required must be an array' });
  }
  return errors;
}

export function manifestFieldErrors(data: unknown): FieldError[] {
  const rec = asRecord(data);
  if (!rec) return [{ loc: '(root)', msg: 'manifest must be an object' }];

  const errors: FieldError[] = [];
  const label = str(rec.label) || str(rec.displayName) || str(rec.name) || str(rec.title);
  const id = str(rec.id) || (label ? slugify(label) : '');
  if (!id && !label) {
    errors.push({ loc: 'label', msg: 'Field required' });
  }

  const version = str(rec.version) || '1.0.0';
  if (!SEMVER.test(version.trim())) {
    errors.push({ loc: 'version', msg: 'must be a semantic version, e.g. 1.0.0' });
  }

  const runner = asRecord(rec.runner) ?? asRecord(rec.runtime);
  if (runner) {
    const type = str(runner.type) || str(runner.engine) || str(runner.runtime) || str(runner.kind);
    if (type && !RUNNER_TYPE_SET.has(type)) {
      errors.push({ loc: 'runner.type', msg: `unsupported runner type '${type}'` });
    }
  }

  const creds = rec.credentialsSchema ?? rec.credentials_schema ?? {};
  const deploy = rec.deploymentConfigSchema ?? rec.deployment_config_schema ?? {};
  errors.push(...validateJsonSchemaDocument(creds, 'credentialsSchema'));
  errors.push(...validateJsonSchemaDocument(deploy, 'deploymentConfigSchema'));
  return errors;
}

export function loadManifest(data: unknown): PluginManifestValidated {
  const errors = manifestFieldErrors(data);
  if (errors.length > 0) {
    throw new Error(errors.map((e) => `${e.loc}: ${e.msg}`).join('; '));
  }
  const rec = asRecord(data) ?? {};
  const label =
    str(rec.label) || str(rec.displayName) || str(rec.name) || str(rec.title) || 'plugin';
  const runner = asRecord(rec.runner) ?? asRecord(rec.runtime) ?? {};
  const type =
    str(runner.type) || str(runner.engine) || str(runner.runtime) || str(runner.kind) || 'node';
  return {
    id: str(rec.id) || slugify(label),
    label,
    version: str(rec.version) || '1.0.0',
    category: str(rec.category) || null,
    description: str(rec.description) || str(rec.summary) || null,
    icon: str(rec.icon) || null,
    runner: { ...runner, type },
    capabilities: Array.isArray(rec.capabilities)
      ? rec.capabilities.map(String)
      : asRecord(rec.capabilities) ?? [],
    credentials_schema: asRecord(rec.credentialsSchema) ?? asRecord(rec.credentials_schema) ?? {},
    deployment_config_schema:
      asRecord(rec.deploymentConfigSchema) ?? asRecord(rec.deployment_config_schema) ?? {},
    parent_cloud: str(rec.parent_cloud) || str(rec.parentCloud) || null,
    homepage: str(rec.homepage) || null,
    license: str(rec.license) || null,
    author: str(rec.author) || null,
  };
}

export function manifestToCatalogEntry(manifest: PluginManifestValidated): CloudProviderCatalogEntry {
  const capabilities = manifest.capabilities;
  const caps = Array.isArray(capabilities)
    ? capabilities
    : [
        ...(capabilities.serviceType ? [String(capabilities.serviceType)] : []),
        ...Object.entries(capabilities)
          .filter(([key, value]) => key !== 'serviceType' && value === true)
          .map(([key]) => key),
      ];
  // Cast keeps FastAPI-compatible additive keys (version/category/capabilities/...)
  // without widening CloudProviderCatalogEntry's required catalog fields.
  return {
    id: manifest.id,
    label: manifest.label,
    version: manifest.version,
    category: manifest.category,
    description: manifest.description,
    icon: manifest.icon,
    docs_url: null,
    runtime_targets: ['vm'],
    credential_fields: [],
    regions: [],
    tiers: [],
    capabilities: caps,
    source: 'manifest',
    parent_cloud: manifest.parent_cloud ?? null,
    homepage: manifest.homepage ?? null,
    license: manifest.license ?? null,
    author: manifest.author ?? null,
  } as CloudProviderCatalogEntry;
}
