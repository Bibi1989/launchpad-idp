import {
  BadRequestException,
  Inject,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { eq } from 'drizzle-orm';
import { randomUUID } from 'crypto';

import { Database, DRIZZLE } from '../database/database.module';
import { userCloudCredentials } from '../database/schema';
import { SecretCipherService } from '../common/crypto/secret-cipher.service';

/**
 * Shapes mirrored 1:1 from the FastAPI backend so both control planes serve the
 * same /api/v1 contract:
 *   - CloudNetworkListResponse / CloudSecurityGroupListResponse
 *     (apps/api/app/schemas/user_credentials.py)
 *   - CloudOAuthCapabilities / CloudOAuthSessionStatus / CloudOAuthStartRequest
 *     (apps/api/app/schemas/cloud_oauth.py)
 */

export interface CloudNetworkOption {
  id: string;
  name: string;
  cidr: string | null;
  is_default: boolean;
  region: string | null;
}

export interface CloudNetworkListResponse {
  provider: string;
  region: string | null;
  networks: CloudNetworkOption[];
}

export interface CloudSecurityGroupOption {
  id: string;
  name: string;
  vpc_id: string | null;
  description: string | null;
  region: string | null;
}

export interface CloudSecurityGroupListResponse {
  provider: string;
  region: string | null;
  vpc_id: string | null;
  security_groups: CloudSecurityGroupOption[];
}

export interface CloudOAuthCapabilities {
  gcp: boolean;
  aws: boolean;
  azure: boolean;
  note: string;
}

export type CloudOAuthProviderName = 'gcp' | 'aws' | 'azure';

export interface CloudOAuthStartRequest {
  provider: CloudOAuthProviderName;
  aws_start_url?: string | null;
  aws_region?: string | null;
  aws_account_id?: string | null;
  aws_role_name?: string | null;
  azure_tenant_id?: string | null;
  azure_subscription_id?: string | null;
}

export interface CloudOAuthSessionStatus {
  session_id: string;
  provider: CloudOAuthProviderName;
  status: 'pending' | 'succeeded' | 'failed';
  message: string | null;
  email: string | null;
  label: string | null;
}

interface CloudOAuthSessionRecord extends CloudOAuthSessionStatus {
  user_id: string;
}

const CLOUD_OAUTH_NOTE =
  'Interactive login opens a browser on the API host ' +
  '(use when Launchpad API runs on your machine).';

@Injectable()
export class UserCredentialsService {
  // Transient OAuth session store. FastAPI persists these in Redis with a TTL
  // (apps/api/app/services/cloud_oauth.py); an in-memory map mirrors that
  // transient contract for this control plane.
  private readonly oauthSessions = new Map<string, CloudOAuthSessionRecord>();

  constructor(
    @Inject(DRIZZLE) private readonly db: Database,
    private readonly config: ConfigService,
    private readonly cipher: SecretCipherService,
  ) {}

  /**
   * Decode the ``encrypted_payload`` column. FastAPI Fernet-encrypts it, so we MUST
   * decrypt (with the shared key) to read credentials another backend saved. Falls
   * back to plaintext JSON for any legacy rows this backend wrote before this fix.
   */
  private decodePayload(raw: string): Record<string, string> {
    try {
      return JSON.parse(this.cipher.decrypt(raw));
    } catch (_) {
      try {
        return JSON.parse(raw); // legacy plaintext row
      } catch (_) {
        return {};
      }
    }
  }

  /** Encrypt the payload so the OTHER backend (FastAPI) can decrypt it (shared vault). */
  private encodePayload(payload: Record<string, string>): string {
    return this.cipher.encrypt(JSON.stringify(payload));
  }

  async listForUser(userId: string): Promise<Record<string, boolean>> {
    const [row] = await this.db
      .select()
      .from(userCloudCredentials)
      .where(eq(userCloudCredentials.userId, userId));

    if (!row || !row.encryptedPayload) return {};
    const parsed = this.decodePayload(row.encryptedPayload);
    const result: Record<string, boolean> = {};
    for (const k of Object.keys(parsed)) {
      if (parsed[k]) result[k] = true;
    }
    return result;
  }

  async getCredentials(userId: string): Promise<Record<string, string>> {
    const [row] = await this.db
      .select()
      .from(userCloudCredentials)
      .where(eq(userCloudCredentials.userId, userId));

    if (!row || !row.encryptedPayload) return {};
    return this.decodePayload(row.encryptedPayload);
  }

  async setCredential(userId: string, key: string, value: string): Promise<void> {
    const [existing] = await this.db
      .select()
      .from(userCloudCredentials)
      .where(eq(userCloudCredentials.userId, userId));

    const now = new Date();
    const currentPayload: Record<string, string> =
      existing && existing.encryptedPayload ? this.decodePayload(existing.encryptedPayload) : {};

    currentPayload[key] = value;
    const newPayloadStr = this.encodePayload(currentPayload);

    if (existing) {
      await this.db
        .update(userCloudCredentials)
        .set({ encryptedPayload: newPayloadStr, updatedAt: now })
        .where(eq(userCloudCredentials.id, existing.id));
    } else {
      await this.db.insert(userCloudCredentials).values({
        id: randomUUID(),
        userId,
        encryptedPayload: newPayloadStr,
        createdAt: now,
        updatedAt: now,
      });
    }
  }

  async deleteCredential(userId: string, key: string): Promise<void> {
    const [existing] = await this.db
      .select()
      .from(userCloudCredentials)
      .where(eq(userCloudCredentials.userId, userId));

    if (!existing || !existing.encryptedPayload) return;
    const currentPayload = this.decodePayload(existing.encryptedPayload);
    delete currentPayload[key];
    const now = new Date();
    await this.db
      .update(userCloudCredentials)
      .set({ encryptedPayload: this.encodePayload(currentPayload), updatedAt: now })
      .where(eq(userCloudCredentials.id, existing.id));
  }
}
