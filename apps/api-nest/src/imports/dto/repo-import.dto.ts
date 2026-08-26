import { Type } from 'class-transformer';
import {
  IsArray,
  IsBoolean,
  IsInt,
  IsNotEmpty,
  IsOptional,
  IsString,
  IsUUID,
  Matches,
  Max,
  MaxLength,
  Min,
  MinLength,
  ValidateNested,
} from 'class-validator';

export class RepoRefDto {
  @IsString()
  @MinLength(8)
  @MaxLength(512)
  git_repo_url!: string;

  @IsString()
  @IsOptional()
  @MinLength(1)
  @MaxLength(256)
  git_branch?: string = 'main';

  @IsString()
  @IsOptional()
  @MaxLength(64)
  name?: string;

  @IsInt()
  @Min(1)
  @IsOptional()
  github_installation_id?: number;
}

export class RepoImportCreateRequestDto {
  @IsString()
  @MinLength(8)
  @MaxLength(512)
  git_repo_url!: string;

  @IsString()
  @IsOptional()
  @MinLength(1)
  @MaxLength(256)
  git_branch?: string = 'main';

  @IsBoolean()
  @IsOptional()
  use_github_app_token?: boolean = true;

  @IsInt()
  @Min(1)
  @IsOptional()
  github_installation_id?: number;

  @IsArray()
  @ValidateNested({ each: true })
  @Type(() => RepoRefDto)
  @IsOptional()
  repos?: RepoRefDto[] = [];

  effectiveRepos(): RepoRefDto[] {
    const primary: RepoRefDto = {
      git_repo_url: this.git_repo_url,
      git_branch: this.git_branch || 'main',
      github_installation_id: this.github_installation_id,
    };
    const seen = new Set<string>([primary.git_repo_url]);
    const out: RepoRefDto[] = [primary];
    for (const ref of this.repos || []) {
      if (!seen.has(ref.git_repo_url)) {
        seen.add(ref.git_repo_url);
        out.push(ref);
      }
    }
    return out;
  }
}

export class ServiceOverrideDto {
  @IsString()
  @IsNotEmpty()
  id!: string;

  @IsBoolean()
  @IsOptional()
  enabled?: boolean = true;

  @IsInt()
  @Min(1)
  @Max(65535)
  @IsOptional()
  port?: number;

  @IsBoolean()
  @IsOptional()
  is_preview_target?: boolean = false;

  @IsString()
  @IsOptional()
  @MaxLength(64)
  name?: string;
}

export class EnvVarOverrideDto {
  @IsString()
  @MinLength(1)
  @MaxLength(128)
  key!: string;

  @IsString()
  @IsOptional()
  @MaxLength(4096)
  value?: string = '';
}

export class DatastoreImportConfigDto {
  @IsString()
  @MinLength(2)
  @MaxLength(32)
  kind!: string;

  @IsString()
  @IsOptional()
  @MaxLength(32)
  placement?: string = 'in_cluster';

  @IsString()
  @IsOptional()
  @MaxLength(2048)
  connection_url?: string;

  // Optional existing Kubernetes secret name for an external datastore; its keys
  // are injected via envFrom (mirrors FastAPI DataStoreDependency.secret_ref).
  @IsString()
  @IsOptional()
  @MaxLength(512)
  secret_ref?: string;
}

export class RepoImportSaveRequestDto {
  @IsString()
  @MinLength(3)
  @MaxLength(64)
  @Matches(/^[a-z][a-z0-9-]*$/, {
    message: 'name must start with a lowercase letter and contain only lowercase letters, numbers, and hyphens',
  })
  name!: string;

  @IsArray()
  @ValidateNested({ each: true })
  @Type(() => ServiceOverrideDto)
  @IsOptional()
  services?: ServiceOverrideDto[] = [];

  @IsBoolean()
  @IsOptional()
  ensure_local_cluster?: boolean = true;

  @IsString()
  @IsOptional()
  @MaxLength(32)
  runtime_mode?: string = 'kubernetes';

  @IsString()
  @IsOptional()
  @MaxLength(32)
  iac_engine?: string = 'launch_script';

  @IsBoolean()
  @IsOptional()
  enable_iac?: boolean = true;

  @IsBoolean()
  @IsOptional()
  enable_cicd?: boolean = false;

  @IsString()
  @IsOptional()
  @MaxLength(16)
  cicd_platform?: string = 'github';

  @IsUUID()
  @IsOptional()
  project_id?: string;

  @IsArray()
  @ValidateNested({ each: true })
  @Type(() => EnvVarOverrideDto)
  @IsOptional()
  env_vars?: EnvVarOverrideDto[] = [];

  @IsArray()
  @ValidateNested({ each: true })
  @Type(() => DatastoreImportConfigDto)
  @IsOptional()
  datastores?: DatastoreImportConfigDto[] = [];

  @IsString()
  @IsOptional()
  @MaxLength(32)
  process_strategy?: string = 'docker';

  @IsString()
  @IsOptional()
  @MaxLength(32)
  reverse_proxy?: string = 'none';

  // Link mode: reference the repos (track changes, re-clone on deploy) instead of
  // freezing the imported source (mirrors FastAPI RepoImportSaveRequest.link_mode).
  @IsBoolean()
  @IsOptional()
  link_mode?: boolean = false;
}

export interface RepoImportSessionReadDto {
  import_id: string;
  git_repo_url: string;
  git_branch: string;
  commit_sha: string;
  layout: string;
  detection: Record<string, any>;
  services: Record<string, any>[];
  created_at: string | null;
  datastore_suggestions: Record<string, any>[];
  repos: string[];
  service_graph?: Record<string, any>;
  mermaid?: string;
}

export interface RepoImportSaveResultDto {
  workspace_id: string;
  name: string;
  durable_dir: string;
  preview_service: string | null;
  files_generated: number;
  runtime_mode: string;
  iac_engine: string;
  cluster_ready: boolean;
  message: string;
}
