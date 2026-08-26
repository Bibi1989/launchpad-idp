import { Inject, Injectable } from '@nestjs/common';
import { eq } from 'drizzle-orm';

import { Database, DRIZZLE } from '../database/database.module';
import { provisioningWorkspaces } from '../database/schema';

@Injectable()
export class K8sService {
  constructor(@Inject(DRIZZLE) private readonly db: Database) {}

  private async workspaceProvider(workspaceId: string): Promise<string> {
    const [ws] = await this.db
      .select({ provider: provisioningWorkspaces.provider })
      .from(provisioningWorkspaces)
      .where(eq(provisioningWorkspaces.id, workspaceId));
    return ws?.provider || 'local';
  }

  /**
   * Cluster context for a workspace, matching the FastAPI k8s context response.
   * This control plane has no live cluster, so the status reflects an unconnected
   * cluster (the shape mirrors FastAPI's ClusterContext exactly).
   */
  async getWorkspaceContext(workspaceId: string, namespace?: string): Promise<any> {
    const provider = await this.workspaceProvider(workspaceId);
    const targetNs = namespace || `lp-${workspaceId.substring(0, 8)}`;
    return {
      workspace_id: workspaceId,
      provider,
      cluster_name: provider === 'local' ? 'launchpad-local' : `${provider}-cluster`,
      context_name: provider === 'local' ? 'kind-launchpad' : `${provider}-context`,
      region: null,
      status: 'disconnected',
      node_count: 0,
      control_plane_health: 'unknown',
      k8s_version: null,
      last_synced_at: null,
      error_message: 'No live cluster connection in this control plane',
      target_namespace: targetNs,
    };
  }

  /**
   * Categorized resource grid for a workspace. FastAPI returns a flat list of grid
   * items; without a live cluster this is empty.
   */
  async getWorkspaceResources(_workspaceId: string, _namespace?: string): Promise<any[]> {
    return [];
  }

  async getClusterHealth(): Promise<any> {
    return {
      status: 'HEALTHY',
      provider: 'kind',
      nodes: [
        {
          name: 'launchpad-control-plane',
          status: 'Ready',
          roles: ['control-plane'],
        },
      ],
    };
  }

  async getWorkloads(namespace?: string): Promise<any[]> {
    return [
      {
        name: 'web-app',
        kind: 'Deployment',
        namespace: namespace || 'default',
        replicas: 1,
        availableReplicas: 1,
        status: 'RUNNING',
      },
    ];
  }

  async deleteResource(payload: any): Promise<any> {
    return {
      deleted: true,
      kind: payload.kind,
      name: payload.name,
      namespace: payload.namespace || 'default',
    };
  }

  /**
   * Sample namespace resource usage, used by the cost-metering scheduled task.
   * Mirrors FastAPI's `provisioner.read_namespace_usage`; returns a mock reading
   * (no real cluster in this control plane).
   */
  async readNamespaceUsage(namespace?: string): Promise<{
    namespace: string;
    cpuMillicores: number;
    memoryMib: number;
    podCount: number;
  }> {
    return {
      namespace: namespace || 'default',
      cpuMillicores: 250,
      memoryMib: 512,
      podCount: 1,
    };
  }

  /**
   * Scan an environment for configuration drift, used by the drift-scan scheduled
   * task. Mirrors FastAPI's `scan_environment`; returns null (no drift) since this
   * control plane has no live cluster to compare desired vs actual state against.
   */
  async scanEnvironmentDrift(_environment: {
    id: string;
    namespaceName?: string | null;
  }): Promise<{ environmentId: string; drifted: boolean; summary: string } | null> {
    return null;
  }
}
