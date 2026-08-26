import { randomUUID } from 'node:crypto';

import { Inject, Injectable } from '@nestjs/common';
import { eq } from 'drizzle-orm';

import { SecretCipherService } from '../common/crypto/secret-cipher.service';
import { DRIZZLE, type Database } from '../database/database.module';
import { providerCredentials } from '../database/schema';

/** Decrypted vault shape: provider id -> { field: value }. */
type Vault = Record<string, Record<string, string>>;

/**
 * NestJS port of app/services/provider_credentials.py, Drizzle-backed and reading the
 * SAME encrypted `provider_credentials` table as FastAPI. Secret values are never
 * returned by status - only which fields are set.
 */
@Injectable()
export class ProviderCredentialsService {
  constructor(
    @Inject(DRIZZLE) private readonly db: Database,
    private readonly cipher: SecretCipherService,
  ) {}

  async statusForUser(userId: string): Promise<Record<string, string[]>> {
    const vault = await this.readVault(userId);
    const status: Record<string, string[]> = {};
    for (const [providerId, fields] of Object.entries(vault)) {
      const setFields = Object.entries(fields)
        .filter(([, value]) => String(value).trim().length > 0)
        .map(([field]) => field);
      if (setFields.length > 0) status[providerId] = setFields;
    }
    return status;
  }

  async getForProvider(userId: string, providerId: string): Promise<Record<string, string>> {
    const vault = await this.readVault(userId);
    return vault[providerId] ?? {};
  }

  /** Merge non-empty fields for a provider; an empty string clears a field. */
  async upsertProvider(
    userId: string,
    providerId: string,
    incoming: Record<string, string>,
  ): Promise<Record<string, string[]>> {
    const vault = await this.readVault(userId);
    const current = { ...(vault[providerId] ?? {}) };
    for (const [key, value] of Object.entries(incoming)) {
      const clean = String(value ?? '').trim();
      if (clean) current[key] = clean;
      else delete current[key];
    }
    if (Object.keys(current).length > 0) vault[providerId] = current;
    else delete vault[providerId];
    await this.writeVault(userId, vault);
    return this.statusForUser(userId);
  }

  async deleteProvider(userId: string, providerId: string): Promise<Record<string, string[]>> {
    const vault = await this.readVault(userId);
    if (providerId in vault) {
      delete vault[providerId];
      await this.writeVault(userId, vault);
    }
    return this.statusForUser(userId);
  }

  private async readVault(userId: string): Promise<Vault> {
    const rows = await this.db
      .select()
      .from(providerCredentials)
      .where(eq(providerCredentials.userId, userId))
      .limit(1);
    const row = rows[0];
    if (!row) return {};
    try {
      const parsed = JSON.parse(this.cipher.decrypt(row.encryptedPayload));
      return this.coerceVault(parsed);
    } catch {
      // Unreadable vault (e.g. key changed) -> treat as empty rather than crash.
      return {};
    }
  }

  private async writeVault(userId: string, vault: Vault): Promise<void> {
    const encryptedPayload = this.cipher.encrypt(JSON.stringify(vault));
    const existing = await this.db
      .select({ id: providerCredentials.id })
      .from(providerCredentials)
      .where(eq(providerCredentials.userId, userId))
      .limit(1);
    if (existing[0]) {
      await this.db
        .update(providerCredentials)
        .set({ encryptedPayload, updatedAt: new Date() })
        .where(eq(providerCredentials.userId, userId));
    } else {
      // The real table has no DB default for id (FastAPI generates it app-side), so we
      // generate one here too rather than relying on a column default.
      await this.db.insert(providerCredentials).values({ id: randomUUID(), userId, encryptedPayload });
    }
  }

  private coerceVault(value: unknown): Vault {
    if (!value || typeof value !== 'object') return {};
    const out: Vault = {};
    for (const [providerId, fields] of Object.entries(value as Record<string, unknown>)) {
      if (fields && typeof fields === 'object') {
        out[providerId] = Object.fromEntries(
          Object.entries(fields as Record<string, unknown>).map(([k, v]) => [k, String(v)]),
        );
      }
    }
    return out;
  }
}
