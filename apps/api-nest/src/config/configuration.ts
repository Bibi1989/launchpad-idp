/**
 * Central runtime configuration, read once from environment variables.
 *
 * Names mirror the FastAPI settings so the two backends can share the same .env
 * (JWT_SECRET, JWT_ALGORITHM, CORS_ORIGINS, ...). Anything specific to this app is
 * prefixed clearly (NEST_PORT).
 */
export interface AppConfig {
  port: number;
  globalPrefix: string;
  corsOrigins: string[];
  jwt: {
    secret: string;
    algorithm: string;
    issuer: string;
  };
  database: {
    url: string;
  };
  // Same key the FastAPI app uses to derive its Fernet key (base64url(sha256(key))).
  secretsEncryptionKey: string;
  activeBackend: string;
}

/**
 * The FastAPI DATABASE_URL is SQLAlchemy-flavoured (postgresql+asyncpg://...).
 * The Drizzle `postgres` driver wants a plain postgres URL, so drop the +driver.
 */
function normalizePostgresUrl(raw: string | undefined): string {
  const url = raw ?? 'postgresql://launchpad:launchpad@localhost:5432/launchpad';
  return url.replace(/^postgresql\+\w+:\/\//, 'postgresql://');
}

function parseCorsOrigins(raw: string | undefined): string[] {
  if (!raw) return ['http://localhost:3000'];
  return raw
    .split(',')
    .map((origin) => origin.trim())
    .filter(Boolean);
}

export function loadConfiguration(): AppConfig {
  return {
    // NEST_PORT keeps the NestJS app on its own port so it can run next to FastAPI.
    port: Number(process.env.NEST_PORT ?? 8001),
    globalPrefix: 'api/v1',
    corsOrigins: parseCorsOrigins(process.env.CORS_ORIGINS),
    jwt: {
      // Same secret/algorithm/issuer as the FastAPI app -> tokens are interchangeable.
      secret: process.env.JWT_SECRET ?? 'dev-only-jwt-secret-change-me-in-production',
      algorithm: process.env.JWT_ALGORITHM ?? 'HS256',
      issuer: 'launchpad-idp',
    },
    database: {
      url: normalizePostgresUrl(process.env.DATABASE_URL),
    },
    secretsEncryptionKey:
      process.env.SECRETS_ENCRYPTION_KEY ??
      process.env.LAUNCHPAD_DEV_SECRET_KEY ??
      'dev-only-change-me-to-a-long-random-string',
    activeBackend: process.env.ACTIVE_BACKEND ?? 'fastapi',
  };
}
