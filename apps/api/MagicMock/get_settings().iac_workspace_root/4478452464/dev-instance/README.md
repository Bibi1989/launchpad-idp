# dev-instance - Local running instance (local_machine)

## Run on compute target

Runtime mode: `running_instance` / kind: `local_machine`.

Launchpad deploys a container image to the selected compute target (local Docker, SSH VM, or managed container service). No Kubernetes manifests are generated for this workspace.

When you are ready for managed Kubernetes, reopen **Provision** and choose the
Kubernetes runtime with a cloud provider (or local Kubernetes).
