import { Injectable, NotFoundException } from '@nestjs/common';

import { findProvider, PROVIDER_CATALOG } from './data/provider-catalog';
import { PROVISIONING_TOOLS, toolsForCloud } from './data/provisioning-tools';
import { servicesFor } from './data/provider-services';
import { adapterIdFor, catalogOverlayFor, expandServicePlugins } from './data/service-plugins';
import type {
  CloudProviderCatalogEntry,
  CloudServiceGroup,
  ProvisioningTool,
} from './cloud-providers.types';

/**
 * Read-only catalog for the multi-cloud provider engine. This is the NestJS port of
 * the FastAPI registry/tools/services metadata - same data, same shapes.
 */
@Injectable()
export class CloudProvidersService {
  listProviders(): CloudProviderCatalogEntry[] {
    return expandServicePlugins(PROVIDER_CATALOG);
  }

  /** One provider, with its service groups attached (matches FastAPI's /{id}). */
  getProvider(id: string): CloudProviderCatalogEntry & { services: CloudServiceGroup[] } {
    const adapter = this.requireProvider(id);
    const overlay = catalogOverlayFor(id, adapter);
    return {
      ...overlay,
      services: overlay.services?.length ? overlay.services : servicesFor(adapter.id),
    };
  }

  getServices(id: string): CloudServiceGroup[] {
    const adapter = this.requireProvider(id);
    const overlay = catalogOverlayFor(id, adapter);
    if (overlay.services?.length) return overlay.services;
    return servicesFor(adapter.id);
  }

  getTools(id: string): ProvisioningTool[] {
    const adapter = this.requireProvider(id);
    return toolsForCloud(adapter.id);
  }

  listAllTools(): ProvisioningTool[] {
    return PROVISIONING_TOOLS;
  }

  private requireProvider(id: string): CloudProviderCatalogEntry {
    const provider = findProvider(adapterIdFor(id));
    if (!provider) {
      throw new NotFoundException(`unknown provider '${id}'`);
    }
    return provider;
  }
}
