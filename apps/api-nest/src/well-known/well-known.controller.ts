import { Controller, Get } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { ApiTags } from '@nestjs/swagger';

@ApiTags('well-known')
@Controller('.well-known')
export class WellKnownController {
  constructor(private readonly config: ConfigService) {}

  @Get('openid-configuration')
  getOpenIdConfiguration() {
    const issuer = (
      this.config.get<string>('launchpad_oidc_issuer_url') || 'http://localhost:8000'
    ).replace(/\/+$/, '');

    return {
      issuer,
      jwks_uri: `${issuer}/.well-known/jwks.json`,
      response_types_supported: ['id_token'],
      subject_types_supported: ['public'],
      id_token_signing_alg_values_supported: ['RS256'],
      scopes_supported: ['openid'],
      claims_supported: [
        'iss',
        'sub',
        'aud',
        'exp',
        'iat',
        'nbf',
        'jti',
        'workspace_id',
        'org_id',
        'environment',
      ],
    };
  }

  @Get('jwks.json')
  getJwks() {
    return {
      keys: [],
    };
  }
}
