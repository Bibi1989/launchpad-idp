import { homedir } from 'node:os';
import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';

import { Inject, Injectable } from '@nestjs/common';
import { and, eq, or } from 'drizzle-orm';

import { DRIZZLE, type Database } from '../database/database.module';
import { pluginManifests } from '../database/schema';
import type { CloudProviderCatalogEntry } from '../cloud-providers/cloud-providers.types';
import {
  loadManifest,
  manifestToCatalogEntry,
  type PluginManifestValidated,
} from './plugin-manifest.schema';

@Injectable()
export class PluginsService {
  constructor(@Inject(DRIZZLE) private readonly db: Database) {}

  bundleDir(orgId: string, pluginId: string): string {
    return join(homedir(), '.launchpad', 'plugins', orgId, pluginId);
  }

  async upsert(
    orgId: string | null,
    manifest: PluginManifestValidated,
    options: { ownerUserId?: string; visibility?: 'private' | 'public' } = {},
  ): Promise<PluginManifestValidated> {
    const visibility = options.visibility === 'public' ? 'public' : 'private';
    const ownerUserId = options.ownerUserId;
    const scopedOrgId = ownerUserId ? null : orgId;
    if (!scopedOrgId && !ownerUserId) {
      throw new Error('orgId or ownerUserId is required');
    }
    const bundlePath = ownerUserId
      ? join(homedir(), '.launchpad', 'plugins', 'users', ownerUserId, manifest.id)
      : this.bundleDir(scopedOrgId!, manifest.id);
    await mkdir(bundlePath, { recursive: true });
    const payload = JSON.stringify(manifest);
    const existing = ownerUserId
      ? await this.db
          .select({ id: pluginManifests.id })
          .from(pluginManifests)
          .where(and(eq(pluginManifests.ownerUserId, ownerUserId), eq(pluginManifests.pluginId, manifest.id)))
          .limit(1)
      : await this.db
          .select({ id: pluginManifests.id })
          .from(pluginManifests)
          .where(and(eq(pluginManifests.orgId, scopedOrgId!), eq(pluginManifests.pluginId, manifest.id)))
          .limit(1);

    if (existing[0]) {
      await this.db
        .update(pluginManifests)
        .set({ manifestJson: payload, bundlePath, visibility, updatedAt: new Date() })
        .where(eq(pluginManifests.id, existing[0].id));
    } else {
      await this.db.insert(pluginManifests).values({
        id: randomUUID(),
        orgId: scopedOrgId,
        ownerUserId: ownerUserId ?? null,
        pluginId: manifest.id,
        manifestJson: payload,
        bundlePath,
        visibility,
      });
    }
    return manifest;
  }

  async getRaw(orgId: string, pluginId: string, userId?: string): Promise<Record<string, unknown> | null> {
    const row = await this.accessibleRow(pluginId, orgId, userId);
    if (!row) return null;
    try {
      const parsed: unknown = JSON.parse(row.manifestJson);
      return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
        ? (parsed as Record<string, unknown>)
        : null;
    } catch {
      return null;
    }
  }

  async getForCaller(
    pluginId: string,
    orgId: string,
    userId: string,
  ): Promise<{
    manifest: Record<string, unknown>;
    owner: 'user' | 'organization';
    visibility: 'private' | 'public';
    can_edit: boolean;
  } | null> {
    const row = await this.accessibleRow(pluginId, orgId, userId);
    if (!row) return null;
    try {
      const parsed: unknown = JSON.parse(row.manifestJson);
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null;
      const owner = row.ownerUserId ? 'user' : 'organization';
      const visibility = row.visibility === 'public' ? 'public' : 'private';
      const canEdit = row.ownerUserId ? row.ownerUserId === userId : row.orgId === orgId;
      return {
        manifest: parsed as Record<string, unknown>,
        owner,
        visibility,
        can_edit: canEdit,
      };
    } catch {
      return null;
    }
  }

  async delete(orgId: string, pluginId: string, userId?: string): Promise<boolean> {
    const row = await this.accessibleRow(pluginId, orgId, userId);
    if (!row) return false;
    const canEdit = row.ownerUserId ? row.ownerUserId === userId : row.orgId === orgId;
    if (!canEdit) return false;
    await this.db.delete(pluginManifests).where(eq(pluginManifests.id, row.id));
    return true;
  }

  async catalogEntries(orgId: string, userId?: string): Promise<CloudProviderCatalogEntry[]> {
    const clauses = [eq(pluginManifests.orgId, orgId), eq(pluginManifests.visibility, 'public')];
    if (userId) clauses.push(eq(pluginManifests.ownerUserId, userId));
    const rows = await this.db.select().from(pluginManifests).where(or(...clauses));
    const ranked = new Map<string, { rank: number; entry: CloudProviderCatalogEntry }>();
    for (const row of rows) {
      try {
        const parsed: unknown = JSON.parse(row.manifestJson);
        const entry = manifestToCatalogEntry(loadManifest(parsed));
        const owner = row.ownerUserId ? 'user' : 'organization';
        const visibility = row.visibility === 'public' ? 'public' : 'private';
        const canEdit = row.ownerUserId ? row.ownerUserId === userId : row.orgId === orgId;
        const merged: CloudProviderCatalogEntry = {
          ...entry,
          owner,
          visibility,
          can_edit: canEdit,
        };
        const rank = row.ownerUserId === userId ? 0 : row.orgId === orgId ? 1 : 2;
        const previous = ranked.get(String(entry.id));
        if (!previous || rank < previous.rank) {
          ranked.set(String(entry.id), { rank, entry: merged });
        }
      } catch {
        // skip unreadable rows, matching FastAPI behaviour
      }
    }
    return [...ranked.values()].map((item) => item.entry);
  }

  private async accessibleRow(pluginId: string, orgId: string, userId?: string) {
    if (userId) {
      const own = await this.db
        .select()
        .from(pluginManifests)
        .where(and(eq(pluginManifests.ownerUserId, userId), eq(pluginManifests.pluginId, pluginId)))
        .limit(1);
      if (own[0]) return own[0];
    }
    const org = await this.db
      .select()
      .from(pluginManifests)
      .where(and(eq(pluginManifests.orgId, orgId), eq(pluginManifests.pluginId, pluginId)))
      .limit(1);
    if (org[0]) return org[0];
    const published = await this.db
      .select()
      .from(pluginManifests)
      .where(and(eq(pluginManifests.pluginId, pluginId), eq(pluginManifests.visibility, 'public')))
      .limit(1);
    return published[0] ?? null;
  }
}
