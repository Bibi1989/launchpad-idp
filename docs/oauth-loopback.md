# Multi-cloud OAuth loopback (RFC 8252 + PKCE)

Interactive user login for GCP, AWS IAM Identity Center, and Microsoft Entra ID.
Implementation: `pkg/auth/oauth_loopback/`.

## Layout

```text
pkg/auth/oauth_loopback/
  pkce.py            # verifier / S256 challenge / state
  loopback.py        # 127.0.0.1 ephemeral listener + HTML teardown
  browser.py         # system default browser
  provider.py        # CloudOAuthProviderBase + token helpers
  client.py          # OAuthLoopbackClient.login()
  models.py          # CloudTokenSet (normalized)
  providers/
    gcp.py
    aws.py
    azure.py
```

Optional later: `apps/api/app/services/cloud_oauth.py` encrypts `CloudTokenSet`
into the user credential vault. Do not put loopback HTTP servers inside FastAPI
request handlers.

## Usage

```python
from pkg.auth.oauth_loopback import (
    AwsSsoOAuthProvider,
    AzureOAuthProvider,
    GcpOAuthProvider,
    login_with_provider,
)

# GCP desktop client
tokens = login_with_provider(
    GcpOAuthProvider(
        client_id="....apps.googleusercontent.com",
        client_secret="...",  # optional for desktop; Google still issues one
    ),
    timeout_seconds=180,
)

# AWS IAM Identity Center (dynamic RegisterClient, like AWS CLI PKCE)
tokens = login_with_provider(
    AwsSsoOAuthProvider(
        start_url="https://d-xxxxxxxxxx.awsapps.com/start",
        region="us-east-1",
    ),
)

# Microsoft Entra ID public client
tokens = login_with_provider(
    AzureOAuthProvider(
        client_id="<app-registration-client-id>",
        tenant_id="common",  # or directory tenant GUID / domain
    ),
)

print(tokens.provider, tokens.email, tokens.expires_at, tokens.can_refresh)
```

## Provider registration

### Google Cloud (Desktop)

1. [Google Cloud Console](https://console.cloud.google.com/apis/credentials) → create OAuth client.
2. Application type: **Desktop app**.
3. Copy Client ID (and Client Secret if shown).
4. Loopback redirects (`http://127.0.0.1:<port>/...`) are allowed for Desktop clients; no fixed port registration required ([native apps](https://developers.google.com/identity/protocols/oauth2/native-app)).
5. Enable APIs your scopes need (e.g. Cloud Resource Manager) and configure the OAuth consent screen.
6. Recommended scopes (defaults in code): `openid`, userinfo email/profile, `https://www.googleapis.com/auth/cloud-platform`.

### AWS IAM Identity Center

1. Enable IAM Identity Center in your AWS org; note the **start URL** (`https://...awsapps.com/start`) and **Region**.
2. No static Entra-style app registration is required for the CLI-style path: `AwsSsoOAuthProvider` calls **RegisterClient** (`clientType=public`, `grantTypes=authorization_code,refresh_token`, `redirectUris=[loopback]`) then **CreateToken** with PKCE ([OIDC API](https://docs.aws.amazon.com/singlesignon/latest/OIDCAPIReference/API_RegisterClient.html)).
3. Persist returned `clientId` / `clientSecret` (and region/start URL) with the refresh token if you will refresh offline (same pattern as `~/.aws/sso/cache`).
4. Ensure users are assigned accounts/permission sets in Identity Center.
5. For headless SSH/CI, prefer device code (`--use-device-code` style) later; this package implements the browser PKCE path only.

### Microsoft Entra ID (Azure)

1. Entra admin center → **App registrations** → New registration.
2. Supported account types: single tenant, or multi-tenant / personal as needed.
3. Platform: **Mobile and desktop applications**.
4. Redirect URI: `http://localhost` and/or `http://127.0.0.1` (Entra ignores the port for localhost/[127.0.0.1](https://learn.microsoft.com/en-us/entra/identity-platform/reply-url)). Path may be `/callback` if you differentiate apps.
5. Authentication → **Allow public client flows** = Yes.
6. API permissions: Microsoft Graph `openid` `profile` `offline_access`, plus Azure management as needed (default code uses `https://management.azure.com/user_impersonation`). Grant admin consent if required.
7. Use Application (client) ID; do **not** embed a client secret for this public-client loopback flow.

## Security notes

- Always use PKCE S256 and a random `state`; the loopback server validates `state`.
- Bind only to `127.0.0.1` (never `0.0.0.0`).
- Tear down the listener immediately after success, error, or timeout (default 180s).
- Never log access/refresh/id tokens or AWS `clientSecret`.
- Encrypt tokens at rest (Launchpad Fernet vault) before persisting.
