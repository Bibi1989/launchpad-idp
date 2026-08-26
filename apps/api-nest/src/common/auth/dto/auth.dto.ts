import { IsEmail, IsNotEmpty, IsOptional, IsString, MinLength } from 'class-validator';

export class UserRegisterDto {
  @IsEmail()
  email!: string;

  @IsString()
  @MinLength(6)
  password!: string;

  @IsString()
  @IsNotEmpty()
  displayName!: string;
}

export class UserLoginDto {
  @IsEmail()
  email!: string;

  @IsString()
  @IsNotEmpty()
  password!: string;
}

export class OidcCallbackDto {
  @IsString()
  @IsNotEmpty()
  code!: string;

  @IsString()
  @IsNotEmpty()
  state!: string;
}

export interface AuthConfigResponseDto {
  dev_login_enabled: boolean;
  oidc_enabled: boolean;
  oidc_provider_name: string | null;
}

export interface TokenResponseDto {
  access_token: string;
  token_type: string;
  expires_in: number;
  user?: {
    id: string;
    email: string;
    display_name: string;
    is_active: boolean;
    is_superuser: boolean;
    created_at: string;
  };
  org_id?: string | null;
  org_role?: string | null;
  orgs?: OrgSummaryDto[];
  active_org_id?: string | null;
  needs_org_setup?: boolean;
}

export interface OrgSummaryDto {
  id: string;
  slug: string;
  name: string;
  role: string;
}

export interface MeResponseDto {
  user: {
    id: string;
    email: string;
    display_name: string;
    is_active: boolean;
    is_superuser: boolean;
    created_at: string;
  };
  orgs: OrgSummaryDto[];
  active_org_id: string | null;
  needs_org_setup: boolean;
}
