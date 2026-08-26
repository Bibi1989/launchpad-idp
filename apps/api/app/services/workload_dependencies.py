"""In-cluster datastore manifests and managed-cloud connection strings for workloads."""

from __future__ import annotations

from enum import Enum

from app.schemas.cloud import (
    AwsCloudConfig,
    AzureCloudConfig,
    CloudConfig,
    CosmosApiKind,
    DependencyPlacement,
    GcpCloudConfig,
    LocalCloudConfig,
    SqlDatabaseEngine,
    WorkloadDependenciesConfig,
)


class DataStoreKind(str, Enum):
    POSTGRES = "postgres"
    MYSQL = "mysql"
    MARIADB = "mariadb"
    MONGODB = "mongodb"
    REDIS = "redis"
    KAFKA = "kafka"
    RABBITMQ = "rabbitmq"


def default_workload_dependencies() -> WorkloadDependenciesConfig:
    return WorkloadDependenciesConfig()


def validate_managed_dependencies(
    cloud: CloudConfig,
    dependencies: WorkloadDependenciesConfig,
) -> None:
    """Raise ValueError when managed placement lacks a backing cloud resource."""
    if not dependencies.any_enabled():
        return

    def _require(condition: bool, message: str) -> None:
        if not condition:
            raise ValueError(message)

    if dependencies.postgres.enabled and dependencies.postgres.placement == DependencyPlacement.MANAGED:
        if isinstance(cloud, GcpCloudConfig):
            _require(
                cloud.resources.cloud_sql,
                "Managed Postgres requires Cloud SQL (enable in GCP resources)",
            )
            _require(
                cloud.resources.cloud_sql_engine == SqlDatabaseEngine.POSTGRES,
                "Managed Postgres requires Cloud SQL engine set to postgres",
            )
        elif isinstance(cloud, AwsCloudConfig):
            _require(cloud.resources.rds, "Managed Postgres requires RDS (enable in AWS resources)")
            _require(
                cloud.resources.rds_engine == SqlDatabaseEngine.POSTGRES,
                "Managed Postgres requires RDS engine set to postgres",
            )
        elif isinstance(cloud, LocalCloudConfig):
            raise ValueError("Managed Postgres is not available for local (kind) workspaces")
        else:
            raise ValueError("Managed Postgres is not supported for this cloud provider")

    if dependencies.mysql.enabled and dependencies.mysql.placement == DependencyPlacement.MANAGED:
        if isinstance(cloud, GcpCloudConfig):
            _require(
                cloud.resources.cloud_sql,
                "Managed MySQL requires Cloud SQL (enable in GCP resources)",
            )
            _require(
                cloud.resources.cloud_sql_engine == SqlDatabaseEngine.MYSQL,
                "Managed MySQL requires Cloud SQL engine set to mysql",
            )
        elif isinstance(cloud, AwsCloudConfig):
            _require(cloud.resources.rds, "Managed MySQL requires RDS (enable in AWS resources)")
            _require(
                cloud.resources.rds_engine == SqlDatabaseEngine.MYSQL,
                "Managed MySQL requires RDS engine set to mysql",
            )
        elif isinstance(cloud, LocalCloudConfig):
            raise ValueError("Managed MySQL is not available for local (kind) workspaces")
        else:
            raise ValueError("Managed MySQL is not supported for this cloud provider")

    if dependencies.mariadb.enabled and dependencies.mariadb.placement == DependencyPlacement.MANAGED:
        if isinstance(cloud, GcpCloudConfig):
            raise ValueError("Managed MariaDB is not available on Cloud SQL; use MySQL or in-cluster")
        elif isinstance(cloud, AwsCloudConfig):
            _require(cloud.resources.rds, "Managed MariaDB requires RDS (enable in AWS resources)")
            _require(
                cloud.resources.rds_engine == SqlDatabaseEngine.MARIADB,
                "Managed MariaDB requires RDS engine set to mariadb",
            )
        elif isinstance(cloud, LocalCloudConfig):
            raise ValueError("Managed MariaDB is not available for local (kind) workspaces")
        else:
            raise ValueError("Managed MariaDB is not supported for this cloud provider")

    if dependencies.mongodb.enabled and dependencies.mongodb.placement == DependencyPlacement.MANAGED:
        if isinstance(cloud, AzureCloudConfig):
            _require(
                cloud.resources.cosmos_db,
                "Managed MongoDB requires Cosmos DB (enable in Azure resources)",
            )
            _require(
                cloud.resources.cosmos_api == CosmosApiKind.MONGODB,
                "Managed MongoDB requires Cosmos DB API set to mongodb",
            )
        elif isinstance(cloud, LocalCloudConfig):
            raise ValueError("Managed MongoDB is not available for local (kind) workspaces")
        else:
            raise ValueError(
                "Managed MongoDB requires Cosmos DB on Azure; use in-cluster on GCP/AWS"
            )

    if dependencies.redis.enabled and dependencies.redis.placement == DependencyPlacement.MANAGED:
        if isinstance(cloud, GcpCloudConfig):
            _require(
                cloud.resources.memorystore,
                "Managed Redis requires Memorystore (enable in GCP resources)",
            )
            _require(
                cloud.resources.memorystore_engine.value == "redis",
                "Managed Redis requires Memorystore engine set to redis",
            )
        elif isinstance(cloud, AwsCloudConfig):
            _require(
                cloud.resources.elasticache,
                "Managed Redis requires ElastiCache (enable in AWS resources)",
            )
            _require(
                cloud.resources.elasticache_engine.value == "redis",
                "Managed Redis requires ElastiCache engine set to redis",
            )
        elif isinstance(cloud, AzureCloudConfig):
            _require(
                cloud.resources.redis_cache,
                "Managed Redis requires Azure Cache for Redis (enable in Azure resources)",
            )
        elif isinstance(cloud, LocalCloudConfig):
            raise ValueError("Managed Redis is not available for local (kind) workspaces")
        else:
            raise ValueError("Managed Redis is not supported for this cloud provider")

    # Message brokers support in-cluster or bring-your-own (external) only.
    if dependencies.kafka.enabled and dependencies.kafka.placement == DependencyPlacement.MANAGED:
        raise ValueError("Managed Kafka is not supported; use in-cluster or an external broker URL")
    if dependencies.rabbitmq.enabled and dependencies.rabbitmq.placement == DependencyPlacement.MANAGED:
        raise ValueError("Managed RabbitMQ is not supported; use in-cluster or an external broker URL")


def _in_cluster_kinds(dependencies: WorkloadDependenciesConfig) -> list[DataStoreKind]:
    mapping = {
        DataStoreKind.POSTGRES: dependencies.postgres,
        DataStoreKind.MYSQL: dependencies.mysql,
        DataStoreKind.MARIADB: dependencies.mariadb,
        DataStoreKind.MONGODB: dependencies.mongodb,
        DataStoreKind.REDIS: dependencies.redis,
        DataStoreKind.KAFKA: dependencies.kafka,
        DataStoreKind.RABBITMQ: dependencies.rabbitmq,
    }
    return [
        kind
        for kind, dep in mapping.items()
        if dep.enabled and dep.placement == DependencyPlacement.IN_CLUSTER
    ]


def _managed_kinds(dependencies: WorkloadDependenciesConfig) -> list[DataStoreKind]:
    mapping = {
        DataStoreKind.POSTGRES: dependencies.postgres,
        DataStoreKind.MYSQL: dependencies.mysql,
        DataStoreKind.MARIADB: dependencies.mariadb,
        DataStoreKind.MONGODB: dependencies.mongodb,
        DataStoreKind.REDIS: dependencies.redis,
    }
    return [
        kind
        for kind, dep in mapping.items()
        if dep.enabled and dep.placement == DependencyPlacement.MANAGED
    ]


def dependency_secret_string_data(
    dependencies: WorkloadDependenciesConfig,
    *,
    name: str,
    cloud: CloudConfig | None = None,
) -> dict[str, str]:
    """Build Secret stringData entries for enabled datastores.

    When an external datastore sets ``secret_ref`` (an existing Kubernetes secret),
    its connection value is injected from that secret via ``envFrom`` at the
    deployment layer, so it is not inlined here.
    """
    data: dict[str, str] = {}
    app_db = name.replace("-", "_")

    def _ext_url(store) -> str:
        if (getattr(store, "secret_ref", None) or "").strip():
            return ""  # value comes from the referenced secret (envFrom)
        return (store.connection_url or "").strip()

    if dependencies.postgres.enabled:
        if dependencies.postgres.placement == DependencyPlacement.IN_CLUSTER:
            data["POSTGRES_PASSWORD"] = "changeme"
            data["DATABASE_URL"] = (
                f"postgresql://launchpad:changeme@postgres:5432/{app_db}"
            )
        elif dependencies.postgres.placement == DependencyPlacement.EXTERNAL:
            url = _ext_url(dependencies.postgres)
            if url:
                data["DATABASE_URL"] = url
        else:
            data["POSTGRES_PASSWORD"] = "change-me-after-terraform-apply"
            if isinstance(cloud, GcpCloudConfig):
                data["DATABASE_URL"] = (
                    "postgresql://launchpad:change-me@"
                    "${terraform_output:managed_postgres_host}:5432/"
                    f"{app_db}"
                )
            elif isinstance(cloud, AwsCloudConfig):
                data["DATABASE_URL"] = (
                    "postgresql://launchpad:change-me@"
                    "${terraform_output:managed_postgres_host}:5432/"
                    f"{app_db}"
                )
            else:
                data["DATABASE_URL"] = (
                    f"postgresql://launchpad:change-me@MANAGED_POSTGRES_HOST:5432/{app_db}"
                )

    if dependencies.mysql.enabled:
        if dependencies.mysql.placement == DependencyPlacement.IN_CLUSTER:
            data["MYSQL_PASSWORD"] = "changeme"
            data["MYSQL_URL"] = f"mysql://launchpad:changeme@mysql:3306/{app_db}"
            data.setdefault("DATABASE_URL", data["MYSQL_URL"])
        elif dependencies.mysql.placement == DependencyPlacement.EXTERNAL:
            url = _ext_url(dependencies.mysql)
            if url:
                data["MYSQL_URL"] = url
                data.setdefault("DATABASE_URL", url)
        else:
            data["MYSQL_PASSWORD"] = "change-me-after-terraform-apply"
            data["MYSQL_URL"] = (
                f"mysql://launchpad:change-me@${{terraform_output:managed_mysql_host}}:3306/{app_db}"
            )

    if dependencies.mariadb.enabled:
        # MariaDB is wire-compatible with MySQL; applications use the mysql:// scheme.
        if dependencies.mariadb.placement == DependencyPlacement.IN_CLUSTER:
            data["MARIADB_PASSWORD"] = "changeme"
            data["MARIADB_URL"] = f"mysql://launchpad:changeme@mariadb:3306/{app_db}"
            data.setdefault("DATABASE_URL", data["MARIADB_URL"])
        elif dependencies.mariadb.placement == DependencyPlacement.EXTERNAL:
            url = _ext_url(dependencies.mariadb)
            if url:
                data["MARIADB_URL"] = url
                data.setdefault("DATABASE_URL", url)
        else:
            data["MARIADB_PASSWORD"] = "change-me-after-terraform-apply"
            data["MARIADB_URL"] = (
                f"mysql://launchpad:change-me@${{terraform_output:managed_mariadb_host}}:3306/{app_db}"
            )

    if dependencies.mongodb.enabled:
        if dependencies.mongodb.placement == DependencyPlacement.IN_CLUSTER:
            data["MONGODB_PASSWORD"] = "changeme"
            data["MONGODB_URI"] = (
                f"mongodb://launchpad:changeme@mongodb:27017/{app_db}?authSource=admin"
            )
        elif dependencies.mongodb.placement == DependencyPlacement.EXTERNAL:
            url = _ext_url(dependencies.mongodb)
            if url:
                data["MONGODB_URI"] = url
        else:
            data["MONGODB_URI"] = (
                f"mongodb://launchpad:change-me@${{terraform_output:managed_mongodb_host}}:10255/{app_db}"
            )

    if dependencies.redis.enabled:
        if dependencies.redis.placement == DependencyPlacement.IN_CLUSTER:
            data["REDIS_URL"] = "redis://redis:6379/0"
        elif dependencies.redis.placement == DependencyPlacement.EXTERNAL:
            url = _ext_url(dependencies.redis)
            if url:
                data["REDIS_URL"] = url
        else:
            data["REDIS_URL"] = "redis://${terraform_output:managed_redis_host}:6379/0"

    if dependencies.kafka.enabled:
        if dependencies.kafka.placement == DependencyPlacement.EXTERNAL:
            url = (dependencies.kafka.connection_url or "").strip()
            if url:
                data["KAFKA_BROKERS"] = url
                data.setdefault("KAFKA_BOOTSTRAP_SERVERS", url)
        else:  # in-cluster (managed is rejected in validation)
            data["KAFKA_BROKERS"] = "kafka:9092"
            data.setdefault("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")

    if dependencies.rabbitmq.enabled:
        if dependencies.rabbitmq.placement == DependencyPlacement.EXTERNAL:
            url = (dependencies.rabbitmq.connection_url or "").strip()
            if url:
                data["RABBITMQ_URL"] = url
                data.setdefault("AMQP_URL", url)
        else:  # in-cluster (managed is rejected in validation)
            data["RABBITMQ_URL"] = "amqp://guest:guest@rabbitmq:5672/"
            data.setdefault("AMQP_URL", "amqp://guest:guest@rabbitmq:5672/")

    return data


def external_secret_names(dependencies: WorkloadDependenciesConfig) -> list[str]:
    """Existing Kubernetes secret names referenced by external datastores.

    Their keys are injected into the workload via ``envFrom`` at the deployment
    layer (see k8s_bundle), so an operator can supply DATABASE_URL / REDIS_URL etc.
    from a pre-existing secret instead of an inline connection string.
    Order-stable and de-duplicated.
    """
    names: list[str] = []
    for store in (
        dependencies.postgres,
        dependencies.mysql,
        dependencies.mariadb,
        dependencies.mongodb,
        dependencies.redis,
    ):
        if not store.enabled or store.placement != DependencyPlacement.EXTERNAL:
            continue
        ref = (getattr(store, "secret_ref", None) or "").strip()
        if ref and ref not in names:
            names.append(ref)
    return names


def init_container_wait_blocks(kinds: list[DataStoreKind]) -> str:
    """Return initContainer YAML blocks (leading newline) for in-cluster datastores."""
    if not kinds:
        return ""
    blocks: list[str] = []
    port_map = {
        DataStoreKind.POSTGRES: ("wait-for-postgres", "postgres", 5432),
        DataStoreKind.MYSQL: ("wait-for-mysql", "mysql", 3306),
        DataStoreKind.MARIADB: ("wait-for-mariadb", "mariadb", 3306),
        DataStoreKind.MONGODB: ("wait-for-mongodb", "mongodb", 27017),
        DataStoreKind.REDIS: ("wait-for-redis", "redis", 6379),
        DataStoreKind.KAFKA: ("wait-for-kafka", "kafka", 9092),
        DataStoreKind.RABBITMQ: ("wait-for-rabbitmq", "rabbitmq", 5672),
    }
    for kind in kinds:
        name, host, port = port_map[kind]
        blocks.append(
            f"""\
        - name: {name}
          image: busybox:1.36
          imagePullPolicy: IfNotPresent
          command:
            - sh
            - -c
            - |
              count=0
              until nc -z {host} {port}; do
                count=$((count+1))
                if [ $count -ge 30 ]; then
                  echo "Timed out waiting for {host}:{port} after 60s"
                  exit 1
                fi
                echo "waiting for {host}:{port} ($count/30)..."
                sleep 2
              done
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 50m
              memory: 64Mi
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            runAsNonRoot: true
            runAsUser: 65534
            capabilities:
              drop:
                - ALL"""
        )
    return "\n" + "\n".join(blocks)


def in_cluster_manifest_files(
    *,
    ns: str,
    name: str,
    kinds: list[DataStoreKind],
) -> dict[str, str]:
    """Return filename → YAML content for in-cluster companion workloads."""
    files: dict[str, str] = {}
    for kind in kinds:
        if kind == DataStoreKind.POSTGRES:
            files["postgres-deployment.yaml"] = _postgres_deployment_yaml(ns, name)
            files["postgres-service.yaml"] = _datastore_service_yaml(ns, name, "postgres", 5432)
        elif kind == DataStoreKind.MYSQL:
            files["mysql-deployment.yaml"] = _mysql_deployment_yaml(ns, name)
            files["mysql-service.yaml"] = _datastore_service_yaml(ns, name, "mysql", 3306)
        elif kind == DataStoreKind.MARIADB:
            files["mariadb-deployment.yaml"] = _mariadb_deployment_yaml(ns, name)
            files["mariadb-service.yaml"] = _datastore_service_yaml(ns, name, "mariadb", 3306)
        elif kind == DataStoreKind.MONGODB:
            files["mongodb-deployment.yaml"] = _mongodb_deployment_yaml(ns, name)
            files["mongodb-service.yaml"] = _datastore_service_yaml(ns, name, "mongodb", 27017)
        elif kind == DataStoreKind.REDIS:
            files["redis-deployment.yaml"] = _redis_deployment_yaml(ns, name)
            files["redis-service.yaml"] = _datastore_service_yaml(ns, name, "redis", 6379)
        elif kind == DataStoreKind.KAFKA:
            files["kafka-deployment.yaml"] = _kafka_deployment_yaml(ns, name)
            files["kafka-service.yaml"] = _datastore_service_yaml(ns, name, "kafka", 9092)
        elif kind == DataStoreKind.RABBITMQ:
            files["rabbitmq-deployment.yaml"] = _rabbitmq_deployment_yaml(ns, name)
            files["rabbitmq-service.yaml"] = _datastore_service_yaml(ns, name, "rabbitmq", 5672)
    return files


def _datastore_labels(name: str, component: str, indent: int = 4) -> str:
    pad = " " * indent
    return (
        f"{pad}app: {component}\n"
        f"{pad}app.kubernetes.io/name: {component}\n"
        f"{pad}app.kubernetes.io/instance: {name}\n"
        f"{pad}launchpad.io/component: datastore\n"
        f"{pad}launchpad.io/environment-name: {name}\n"
        f"{pad}launchpad.io/managed-by: launchpad-idp\n"
        f"{pad}launchpad.io/ephemeral: \"true\"\n"
    )


def _datastore_service_yaml(ns: str, name: str, component: str, port: int) -> str:
    return f"""\
apiVersion: v1
kind: Service
metadata:
  name: {component}
  namespace: {ns}
  labels:
{_datastore_labels(name, component).rstrip()}
spec:
  type: ClusterIP
  selector:
    app: {component}
    launchpad.io/managed-by: launchpad-idp
  ports:
    - name: {component}
      port: {port}
      targetPort: {port}
"""


def _postgres_deployment_yaml(ns: str, name: str) -> str:
    app_db = name.replace("-", "_")
    return f"""\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: postgres
  namespace: {ns}
  labels:
{_datastore_labels(name, "postgres").rstrip()}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: postgres
      launchpad.io/managed-by: launchpad-idp
  template:
    metadata:
      labels:
{_datastore_labels(name, "postgres", indent=8).rstrip()}
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 999
        runAsGroup: 999
        fsGroup: 999
      containers:
        - name: postgres
          image: postgres:16-alpine
          imagePullPolicy: IfNotPresent
          ports:
            - name: postgres
              containerPort: 5432
          env:
            - name: PGDATA
              value: /var/lib/postgresql/data/pgdata
            - name: POSTGRES_USER
              value: launchpad
            - name: POSTGRES_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: app-secrets
                  key: POSTGRES_PASSWORD
            - name: POSTGRES_DB
              value: {app_db}
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
            limits:
              cpu: 500m
              memory: 512Mi
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: false
            runAsNonRoot: true
            runAsUser: 999
            capabilities:
              drop:
                - ALL
          volumeMounts:
            - name: data
              mountPath: /var/lib/postgresql/data
              # subPath keeps PGDATA off the volume root so a PVC's lost+found
              # never triggers "initdb: directory not empty".
              subPath: pgdata
      volumes:
        - name: data
          emptyDir: {{}}
"""


def _mysql_deployment_yaml(ns: str, name: str) -> str:
    app_db = name.replace("-", "_")
    return f"""\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mysql
  namespace: {ns}
  labels:
{_datastore_labels(name, "mysql").rstrip()}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: mysql
      launchpad.io/managed-by: launchpad-idp
  template:
    metadata:
      labels:
{_datastore_labels(name, "mysql", indent=8).rstrip()}
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 999
        runAsGroup: 999
        fsGroup: 999
      containers:
        - name: mysql
          image: mysql:8.4
          imagePullPolicy: IfNotPresent
          ports:
            - name: mysql
              containerPort: 3306
          env:
            - name: MYSQL_ROOT_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: app-secrets
                  key: MYSQL_PASSWORD
            - name: MYSQL_DATABASE
              value: {app_db}
            - name: MYSQL_USER
              value: launchpad
            - name: MYSQL_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: app-secrets
                  key: MYSQL_PASSWORD
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
            limits:
              cpu: 500m
              memory: 512Mi
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: false
            runAsNonRoot: true
            runAsUser: 999
            capabilities:
              drop:
                - ALL
          volumeMounts:
            - name: data
              mountPath: /var/lib/mysql
      volumes:
        - name: data
          emptyDir: {{}}
"""


def _mariadb_deployment_yaml(ns: str, name: str) -> str:
    app_db = name.replace("-", "_")
    return f"""\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mariadb
  namespace: {ns}
  labels:
{_datastore_labels(name, "mariadb").rstrip()}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: mariadb
      launchpad.io/managed-by: launchpad-idp
  template:
    metadata:
      labels:
{_datastore_labels(name, "mariadb", indent=8).rstrip()}
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 999
        runAsGroup: 999
        fsGroup: 999
      containers:
        - name: mariadb
          image: mariadb:11
          imagePullPolicy: IfNotPresent
          ports:
            - name: mariadb
              containerPort: 3306
          env:
            - name: MARIADB_ROOT_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: app-secrets
                  key: MARIADB_PASSWORD
            - name: MARIADB_DATABASE
              value: {app_db}
            - name: MARIADB_USER
              value: launchpad
            - name: MARIADB_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: app-secrets
                  key: MARIADB_PASSWORD
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
            limits:
              cpu: 500m
              memory: 512Mi
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: false
            runAsNonRoot: true
            runAsUser: 999
            capabilities:
              drop:
                - ALL
          volumeMounts:
            - name: data
              mountPath: /var/lib/mysql
      volumes:
        - name: data
          emptyDir: {{}}
"""


def _mongodb_deployment_yaml(ns: str, name: str) -> str:
    app_db = name.replace("-", "_")
    return f"""\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mongodb
  namespace: {ns}
  labels:
{_datastore_labels(name, "mongodb").rstrip()}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: mongodb
      launchpad.io/managed-by: launchpad-idp
  template:
    metadata:
      labels:
{_datastore_labels(name, "mongodb", indent=8).rstrip()}
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 999
        runAsGroup: 999
        fsGroup: 999
      containers:
        - name: mongodb
          image: mongo:7
          imagePullPolicy: IfNotPresent
          ports:
            - name: mongodb
              containerPort: 27017
          env:
            - name: MONGO_INITDB_ROOT_USERNAME
              value: launchpad
            - name: MONGO_INITDB_ROOT_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: app-secrets
                  key: MONGODB_PASSWORD
            - name: MONGO_INITDB_DATABASE
              value: {app_db}
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
            limits:
              cpu: 500m
              memory: 512Mi
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: false
            runAsNonRoot: true
            runAsUser: 999
            capabilities:
              drop:
                - ALL
          volumeMounts:
            - name: data
              mountPath: /data/db
      volumes:
        - name: data
          emptyDir: {{}}
"""


def _redis_deployment_yaml(ns: str, name: str) -> str:
    return f"""\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: redis
  namespace: {ns}
  labels:
{_datastore_labels(name, "redis").rstrip()}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: redis
      launchpad.io/managed-by: launchpad-idp
  template:
    metadata:
      labels:
{_datastore_labels(name, "redis", indent=8).rstrip()}
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 999
        runAsGroup: 999
        fsGroup: 999
      containers:
        - name: redis
          image: redis:7-alpine
          imagePullPolicy: IfNotPresent
          ports:
            - name: redis
              containerPort: 6379
          args:
            - redis-server
            - --save
            - ""
            - --appendonly
            - "no"
          resources:
            requests:
              cpu: 50m
              memory: 128Mi
            limits:
              cpu: 250m
              memory: 256Mi
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            runAsNonRoot: true
            runAsUser: 999
            capabilities:
              drop:
                - ALL
          volumeMounts:
            - name: data
              mountPath: /data
      volumes:
        - name: data
          emptyDir: {{}}
"""


def _kafka_deployment_yaml(ns: str, name: str) -> str:
    # Single-node KRaft Kafka (no ZooKeeper) suitable for ephemeral workspaces.
    return f"""\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: kafka
  namespace: {ns}
  labels:
{_datastore_labels(name, "kafka").rstrip()}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: kafka
      launchpad.io/managed-by: launchpad-idp
  template:
    metadata:
      labels:
{_datastore_labels(name, "kafka", indent=8).rstrip()}
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        runAsGroup: 1000
        fsGroup: 1000
      containers:
        - name: kafka
          image: bitnami/kafka:3.7
          imagePullPolicy: IfNotPresent
          ports:
            - name: kafka
              containerPort: 9092
          env:
            - name: KAFKA_CFG_NODE_ID
              value: "0"
            - name: KAFKA_CFG_PROCESS_ROLES
              value: controller,broker
            - name: KAFKA_CFG_CONTROLLER_QUORUM_VOTERS
              value: 0@localhost:9093
            - name: KAFKA_CFG_LISTENERS
              value: PLAINTEXT://:9092,CONTROLLER://:9093
            - name: KAFKA_CFG_ADVERTISED_LISTENERS
              value: PLAINTEXT://kafka:9092
            - name: KAFKA_CFG_LISTENER_SECURITY_PROTOCOL_MAP
              value: CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT
            - name: KAFKA_CFG_CONTROLLER_LISTENER_NAMES
              value: CONTROLLER
            - name: KAFKA_CFG_INTER_BROKER_LISTENER_NAME
              value: PLAINTEXT
            - name: KAFKA_CFG_OFFSETS_TOPIC_REPLICATION_FACTOR
              value: "1"
            - name: ALLOW_PLAINTEXT_LISTENER
              value: "yes"
          resources:
            requests:
              cpu: 250m
              memory: 512Mi
            limits:
              cpu: "1"
              memory: 1Gi
          securityContext:
            allowPrivilegeEscalation: false
            runAsNonRoot: true
            runAsUser: 1000
            capabilities:
              drop:
                - ALL
          volumeMounts:
            - name: data
              mountPath: /bitnami/kafka
      volumes:
        - name: data
          emptyDir: {{}}
"""


def _rabbitmq_deployment_yaml(ns: str, name: str) -> str:
    return f"""\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: rabbitmq
  namespace: {ns}
  labels:
{_datastore_labels(name, "rabbitmq").rstrip()}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: rabbitmq
      launchpad.io/managed-by: launchpad-idp
  template:
    metadata:
      labels:
{_datastore_labels(name, "rabbitmq", indent=8).rstrip()}
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 999
        runAsGroup: 999
        fsGroup: 999
      containers:
        - name: rabbitmq
          image: rabbitmq:3.13-management-alpine
          imagePullPolicy: IfNotPresent
          ports:
            - name: amqp
              containerPort: 5672
            - name: management
              containerPort: 15672
          env:
            - name: RABBITMQ_DEFAULT_USER
              value: guest
            - name: RABBITMQ_DEFAULT_PASS
              value: guest
          resources:
            requests:
              cpu: 100m
              memory: 256Mi
            limits:
              cpu: 500m
              memory: 512Mi
          securityContext:
            allowPrivilegeEscalation: false
            runAsNonRoot: true
            runAsUser: 999
            capabilities:
              drop:
                - ALL
          volumeMounts:
            - name: data
              mountPath: /var/lib/rabbitmq
      volumes:
        - name: data
          emptyDir: {{}}
"""


def managed_connections_readme(
    dependencies: WorkloadDependenciesConfig,
    cloud: CloudConfig,
) -> str:
    kinds = _managed_kinds(dependencies)
    if not kinds:
        return ""
    lines = [
        "# Managed datastore connections",
        "",
        "After `terraform apply`, map outputs into `infra/k8s/manifests/secret.yaml` "
        "or your cloud secret manager:",
        "",
    ]
    if DataStoreKind.POSTGRES in kinds:
        lines.append("- Postgres: `terraform output -raw managed_postgres_connection_url`")
    if DataStoreKind.MYSQL in kinds:
        lines.append("- MySQL: `terraform output -raw managed_mysql_connection_url`")
    if DataStoreKind.MARIADB in kinds:
        lines.append("- MariaDB: `terraform output -raw managed_mariadb_connection_url`")
    if DataStoreKind.MONGODB in kinds:
        lines.append("- MongoDB: `terraform output -raw managed_mongodb_connection_url`")
    if DataStoreKind.REDIS in kinds:
        lines.append("- Redis: `terraform output -raw managed_redis_connection_url`")
    lines.append("")
    if isinstance(cloud, GcpCloudConfig):
        lines.append("GCP managed services: Cloud SQL, Memorystore.")
    elif isinstance(cloud, AwsCloudConfig):
        lines.append("AWS managed services: RDS, ElastiCache.")
    elif isinstance(cloud, AzureCloudConfig):
        lines.append("Azure managed services: Cosmos DB, Azure Cache for Redis.")
    return "\n".join(lines) + "\n"
