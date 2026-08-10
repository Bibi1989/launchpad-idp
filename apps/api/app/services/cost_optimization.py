"""Cost optimization helpers that inject right-sizing policies into K8s manifests."""

from __future__ import annotations

import json

from app.schemas.cloud import (
    CostOptimizationConfig,
    CostResourceConfig,
    ResourceSizingPreset,
    SpotWorkloadPlacement,
)

# (cpu_request, memory_request, cpu_limit, memory_limit)
RESOURCE_PRESETS: dict[ResourceSizingPreset, tuple[str, str, str, str]] = {
    # 256Mi/768Mi floors avoid OOMKilled (exit 137) on Node/Vite preview cold starts.
    ResourceSizingPreset.DEVELOPER: ("100m", "256Mi", "500m", "768Mi"),
    ResourceSizingPreset.BALANCED: ("250m", "512Mi", "500m", "1Gi"),
    ResourceSizingPreset.PERFORMANCE: ("1", "2Gi", "2", "4Gi"),
}


def resolve_resources(config: CostResourceConfig) -> tuple[str, str, str, str]:
    """Return cpu/memory request+limit strings for the selected preset or custom override."""
    if config.preset == ResourceSizingPreset.CUSTOM:
        return (
            config.cpu_request,
            config.memory_request,
            config.cpu_limit,
            config.memory_limit,
        )
    if config.preset in RESOURCE_PRESETS:
        return RESOURCE_PRESETS[config.preset]
    return RESOURCE_PRESETS[ResourceSizingPreset.DEVELOPER]


def apply_preset_to_config(config: CostResourceConfig) -> CostResourceConfig:
    """Materialize preset values onto the resource config for persistence/UI round-trip."""
    if config.preset == ResourceSizingPreset.CUSTOM:
        return config
    cpu_req, mem_req, cpu_lim, mem_lim = resolve_resources(config)
    return config.model_copy(
        update={
            "cpu_request": cpu_req,
            "memory_request": mem_req,
            "cpu_limit": cpu_lim,
            "memory_limit": mem_lim,
        }
    )


def spot_affinity_tolerations_yaml(
    cost: CostOptimizationConfig,
    *,
    indent: int = 6,
) -> str:
    """Render affinity + tolerations block for a pod spec (raw manifests)."""
    if not cost.spot_scheduling.enabled:
        return ""
    pad = " " * indent
    weight = max(1, min(100, cost.spot_scheduling.allocation_percent))
    on_demand_weight = max(1, 100 - weight)

    if cost.spot_scheduling.placement == SpotWorkloadPlacement.STATELESS_NONPROD:
        affinity = f"""\
{pad}affinity:
{pad}  nodeAffinity:
{pad}    requiredDuringSchedulingIgnoredDuringExecution:
{pad}      nodeSelectorTerms:
{pad}        - matchExpressions:
{pad}            - key: capacity-type
{pad}              operator: In
{pad}              values: ["spot", "capacity-optimized", "preemptible"]
"""
    else:
        affinity = f"""\
{pad}affinity:
{pad}  nodeAffinity:
{pad}    preferredDuringSchedulingIgnoredDuringExecution:
{pad}      - weight: {weight}
{pad}        preference:
{pad}          matchExpressions:
{pad}            - key: capacity-type
{pad}              operator: In
{pad}              values: ["spot", "capacity-optimized", "preemptible"]
{pad}      - weight: {on_demand_weight}
{pad}        preference:
{pad}          matchExpressions:
{pad}            - key: capacity-type
{pad}              operator: In
{pad}              values: ["on-demand", "on_demand"]
"""

    tolerations = f"""\
{pad}tolerations:
{pad}  - key: "spot"
{pad}    operator: "Exists"
{pad}    effect: "NoSchedule"
{pad}  - key: "preemptible"
{pad}    operator: "Exists"
{pad}    effect: "NoSchedule"
"""
    return affinity + tolerations


def spot_helm_values_fragment(cost: CostOptimizationConfig) -> str:
    """YAML fragment for Helm values.yaml spot / affinity section."""
    if not cost.spot_scheduling.enabled:
        return """\
spot:
  enabled: false
  allocationPercent: 0
  placement: stateless_nonprod
  provisioner: karpenter

affinity: {}
tolerations: []
"""
    weight = max(1, min(100, cost.spot_scheduling.allocation_percent))
    placement = cost.spot_scheduling.placement.value
    provisioner = cost.spot_scheduling.provisioner.value
    if cost.spot_scheduling.placement == SpotWorkloadPlacement.STATELESS_NONPROD:
        affinity = """\
affinity:
  nodeAffinity:
    requiredDuringSchedulingIgnoredDuringExecution:
      nodeSelectorTerms:
        - matchExpressions:
            - key: capacity-type
              operator: In
              values: ["spot", "capacity-optimized", "preemptible"]
"""
    else:
        on_demand_weight = max(1, 100 - weight)
        affinity = f"""\
affinity:
  nodeAffinity:
    preferredDuringSchedulingIgnoredDuringExecution:
      - weight: {weight}
        preference:
          matchExpressions:
            - key: capacity-type
              operator: In
              values: ["spot", "capacity-optimized", "preemptible"]
      - weight: {on_demand_weight}
        preference:
          matchExpressions:
            - key: capacity-type
              operator: In
              values: ["on-demand", "on_demand"]
"""
    return f"""\
spot:
  enabled: true
  allocationPercent: {weight}
  placement: {placement}
  provisioner: {provisioner}

{affinity.rstrip()}
tolerations:
  - key: "spot"
    operator: "Exists"
    effect: "NoSchedule"
  - key: "preemptible"
    operator: "Exists"
    effect: "NoSchedule"
"""


def idle_shutdown_cronjobs_yaml(ns: str, name: str, app: str) -> str:
    """CronJobs that scale the deployment to 0 outside Mon-Fri 07:00-19:00."""
    labels = f"""\
  labels:
    app: {app}
    app.kubernetes.io/name: {app}
    app.kubernetes.io/instance: {name}
    launchpad.io/managed-by: launchpad-idp
    launchpad.io/cost-policy: idle-shutdown
"""
    return f"""\
apiVersion: batch/v1
kind: CronJob
metadata:
  name: {app}-scale-down
  namespace: {ns}
{labels.rstrip()}
spec:
  # Mon-Fri 19:00 - sleep for evenings + weekends until Monday scale-up
  schedule: "0 19 * * 1-5"
  successfulJobsHistoryLimit: 1
  failedJobsHistoryLimit: 1
  jobTemplate:
    spec:
      template:
        spec:
          serviceAccountName: {app}-scaler
          restartPolicy: OnFailure
          containers:
            - name: scale-down
              image: bitnami/kubectl:1.31
              command:
                - /bin/sh
                - -c
                - kubectl -n {ns} scale deployment/{app} --replicas=0
---
apiVersion: batch/v1
kind: CronJob
metadata:
  name: {app}-scale-up
  namespace: {ns}
{labels.rstrip()}
spec:
  # Mon-Fri 07:00 - wake for business hours
  schedule: "0 7 * * 1-5"
  successfulJobsHistoryLimit: 1
  failedJobsHistoryLimit: 1
  jobTemplate:
    spec:
      template:
        spec:
          serviceAccountName: {app}-scaler
          restartPolicy: OnFailure
          containers:
            - name: scale-up
              image: bitnami/kubectl:1.31
              command:
                - /bin/sh
                - -c
                - kubectl -n {ns} scale deployment/{app} --replicas=2
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {app}-scaler
  namespace: {ns}
{labels.rstrip()}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: {app}-scaler
  namespace: {ns}
{labels.rstrip()}
rules:
  - apiGroups: ["apps"]
    resources: ["deployments", "deployments/scale"]
    verbs: ["get", "patch", "update"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: {app}-scaler
  namespace: {ns}
{labels.rstrip()}
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: Role
  name: {app}-scaler
subjects:
  - kind: ServiceAccount
    name: {app}-scaler
    namespace: {ns}
"""


def karpenter_nodepool_yaml(name: str, allocation_percent: int) -> str:
    """Minimal Karpenter NodePool favoring spot capacity."""
    weight = max(1, min(100, allocation_percent))
    return f"""\
apiVersion: karpenter.sh/v1
kind: NodePool
metadata:
  name: launchpad-{name}-spot
  labels:
    launchpad.io/managed-by: launchpad-idp
    launchpad.io/environment-name: {name}
    launchpad.io/cost-policy: spot
spec:
  weight: {weight}
  template:
    metadata:
      labels:
        capacity-type: spot
        launchpad.io/managed-by: launchpad-idp
    spec:
      requirements:
        - key: capacity-type
          operator: In
          values: ["spot"]
        - key: kubernetes.io/arch
          operator: In
          values: ["amd64"]
      taints:
        - key: spot
          effect: NoSchedule
  limits:
    cpu: "100"
  disruption:
    consolidationPolicy: WhenEmptyOrUnderutilized
    consolidateAfter: 5m
"""


def cluster_autoscaler_notes_yaml(name: str, allocation_percent: int) -> str:
    """Guidance + ASG tag conventions for Cluster Autoscaler spot node groups."""
    return f"""\
# Cluster Autoscaler spot strategy for Launchpad workspace "{name}"
# Target spot allocation: {allocation_percent}%
#
# Tag your Autoscale Groups (or equivalent) so the Cluster Autoscaler can
# discover them, and prefer Spot / Preemptible node groups for this workspace:
#
#   k8s.io/cluster-autoscaler/enabled = true
#   k8s.io/cluster-autoscaler/<cluster-name> = owned
#   k8s.io/cluster-autoscaler/node-template/label/capacity-type = spot
#   k8s.io/cluster-autoscaler/node-template/taint/spot = NoSchedule
#
# Workloads generated by Launchpad include spot tolerations and node affinity
# so pods schedule onto these capacity-type=spot nodes.
"""


def cost_marker_comment(cost: CostOptimizationConfig) -> str:
    """Single-line marker for round-trip of cost suite settings in YAML files."""
    payload = cost.model_dump(mode="json")
    return f"# launchpad-cost-optimization: {json.dumps(payload, separators=(',', ':'))}\n"
