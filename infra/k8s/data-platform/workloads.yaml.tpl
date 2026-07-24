apiVersion: batch/v1
kind: Job
metadata:
  name: oday-data-platform-migrate-__RELEASE_SHORT__
  namespace: oday-dev
  labels:
    app.kubernetes.io/name: oday-data-platform
    app.kubernetes.io/component: migration
    app.kubernetes.io/version: "__RELEASE_SHA__"
  annotations:
    oday.plus/release-sha: "__RELEASE_SHA__"
    oday.plus/image-reference: "__DATA_IMAGE__"
    oday.plus/execution-order: "00-migration"
spec:
  backoffLimit: 1
  activeDeadlineSeconds: 3600
  ttlSecondsAfterFinished: 604800
  template:
    metadata:
      labels:
        app.kubernetes.io/name: oday-data-platform
        app.kubernetes.io/component: migration
        app.kubernetes.io/version: "__RELEASE_SHA__"
      annotations:
        oday.plus/release-sha: "__RELEASE_SHA__"
        oday.plus/image-reference: "__DATA_IMAGE__"
    spec:
      serviceAccountName: oday-data-platform
      automountServiceAccountToken: true
      restartPolicy: Never
      terminationGracePeriodSeconds: 30
      securityContext:
        runAsNonRoot: true
        runAsUser: 65532
        runAsGroup: 65532
        fsGroup: 65532
        seccompProfile:
          type: RuntimeDefault
      initContainers:
        - name: cloud-sql-auth-proxy
          image: "__CLOUD_SQL_PROXY_IMAGE__"
          imagePullPolicy: IfNotPresent
          restartPolicy: Always
          args:
            - "--structured-logs"
            - "--address=0.0.0.0"
            - "--port=5432"
            - "__CLOUD_SQL_INSTANCE__"
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop: ["ALL"]
            readOnlyRootFilesystem: true
            runAsNonRoot: true
          resources:
            requests:
              cpu: 25m
              memory: 64Mi
            limits:
              cpu: 500m
              memory: 512Mi
          startupProbe:
            tcpSocket:
              port: 5432
            failureThreshold: 30
            periodSeconds: 2
          volumeMounts:
            - name: proxy-tmp
              mountPath: /tmp
      containers:
        - name: migration
          image: "__DATA_IMAGE__"
          imagePullPolicy: IfNotPresent
          args: ["migrate"]
          env:
            - name: ODP_RELEASE_SHA
              value: "__RELEASE_SHA__"
            - name: ODP_IMAGE_REFERENCE
              value: "__DATA_IMAGE__"
            - name: ODP_POSTGRES_HOST
              value: "127.0.0.1"
            - name: ODP_POSTGRES_PORT
              value: "5432"
            - name: ODP_POSTGRES_USER
              value: "__POSTGRES_USER__"
            - name: ODP_POSTGRES_DATABASE
              value: "__POSTGRES_DATABASE__"
            - name: ODP_POSTGRES_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: oday-data-platform-runtime
                  key: postgres-password
            - name: ODP_DATA_ENV
              value: production
            - name: ODP_DATA_CLOUD_SQL_PROXY
              value: "true"
            - name: ODP_DATA_CLOUD_SQL_INSTANCE
              value: "__CLOUD_SQL_INSTANCE__"
            - name: ODP_DATA_CLOUD_SQL_CONNECTOR_EVIDENCE
              value: cloud-sql-auth-proxy-sidecar
            - name: ODP_TERMINATION_RECEIPT_PATH
              value: /var/run/oday/termination.log
          resources:
            requests:
              cpu: 100m
              memory: 1Gi
            limits:
              cpu: "2"
              memory: 4Gi
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop: ["ALL"]
            readOnlyRootFilesystem: true
            runAsNonRoot: true
          terminationMessagePath: /var/run/oday/termination.log
          terminationMessagePolicy: File
          volumeMounts:
            - name: runtime
              mountPath: /var/run/oday
            - name: state
              mountPath: /var/lib/oday
      volumes:
        - name: proxy-tmp
          emptyDir: {}
        - name: runtime
          emptyDir:
            sizeLimit: 16Mi
        - name: state
          emptyDir:
            sizeLimit: 1Gi
---
apiVersion: batch/v1
kind: CronJob
metadata:
  name: oday-data-platform-bounded-daily
  namespace: oday-dev
  labels:
    app.kubernetes.io/name: oday-data-platform
    app.kubernetes.io/component: bounded-backfill
    app.kubernetes.io/version: "__RELEASE_SHA__"
  annotations:
    oday.plus/release-sha: "__RELEASE_SHA__"
    oday.plus/image-reference: "__DATA_IMAGE__"
    oday.plus/requires-migration-receipt: "true"
    oday.plus/execution-order: "10-bounded-backfill"
spec:
  schedule: "0 1 * * *"
  timeZone: Etc/UTC
  concurrencyPolicy: Forbid
  startingDeadlineSeconds: 1800
  successfulJobsHistoryLimit: 7
  failedJobsHistoryLimit: 7
  jobTemplate:
    spec:
      backoffLimit: 1
      activeDeadlineSeconds: 14400
      ttlSecondsAfterFinished: 604800
      template:
        metadata:
          labels:
            app.kubernetes.io/name: oday-data-platform
            app.kubernetes.io/component: bounded-backfill
            app.kubernetes.io/version: "__RELEASE_SHA__"
          annotations:
            oday.plus/release-sha: "__RELEASE_SHA__"
            oday.plus/image-reference: "__DATA_IMAGE__"
            oday.plus/requires-migration-receipt: "true"
        spec:
          serviceAccountName: oday-data-platform
          automountServiceAccountToken: true
          restartPolicy: Never
          terminationGracePeriodSeconds: 30
          securityContext:
            runAsNonRoot: true
            runAsUser: 65532
            runAsGroup: 65532
            fsGroup: 65532
            seccompProfile:
              type: RuntimeDefault
          initContainers:
            - name: cloud-sql-auth-proxy
              image: "__CLOUD_SQL_PROXY_IMAGE__"
              imagePullPolicy: IfNotPresent
              restartPolicy: Always
              args:
                - "--structured-logs"
                - "--address=0.0.0.0"
                - "--port=5432"
                - "__CLOUD_SQL_INSTANCE__"
              securityContext:
                allowPrivilegeEscalation: false
                capabilities:
                  drop: ["ALL"]
                readOnlyRootFilesystem: true
                runAsNonRoot: true
              resources:
                requests:
                  cpu: 25m
                  memory: 64Mi
                limits:
                  cpu: 500m
                  memory: 512Mi
              startupProbe:
                tcpSocket:
                  port: 5432
                failureThreshold: 30
                periodSeconds: 2
              volumeMounts:
                - name: proxy-tmp
                  mountPath: /tmp
          containers:
            - name: bounded-backfill
              image: "__DATA_IMAGE__"
              imagePullPolicy: IfNotPresent
              args: ["scheduled"]
              env:
                - name: ODP_RELEASE_SHA
                  value: "__RELEASE_SHA__"
                - name: ODP_IMAGE_REFERENCE
                  value: "__DATA_IMAGE__"
                - name: ODP_DATA_ENV
                  value: production
                - name: ODP_DATA_MONGO_DATABASE
                  value: fongniao_prod
                - name: ODP_DATA_MONGO_URI
                  valueFrom:
                    secretKeyRef:
                      name: oday-data-platform-runtime
                      key: mongodb-uri
                - name: ODP_POSTGRES_HOST
                  value: "127.0.0.1"
                - name: ODP_POSTGRES_PORT
                  value: "5432"
                - name: ODP_POSTGRES_USER
                  value: "__POSTGRES_USER__"
                - name: ODP_POSTGRES_DATABASE
                  value: "__POSTGRES_DATABASE__"
                - name: ODP_POSTGRES_PASSWORD
                  valueFrom:
                    secretKeyRef:
                      name: oday-data-platform-runtime
                      key: postgres-password
                - name: ODP_DATA_RAW_SCHEMA
                  value: fongniao_raw
                - name: ODP_DATA_CONTROL_SCHEMA
                  value: data_plane
                - name: ODP_DATA_BATCH_SIZE
                  value: "5000"
                - name: ODP_DATA_MAX_RECORDS_PER_RUN
                  value: "250000"
                - name: ODP_DATA_CLOUD_SQL_PROXY
                  value: "true"
                - name: ODP_DATA_CLOUD_SQL_INSTANCE
                  value: "__CLOUD_SQL_INSTANCE__"
                - name: ODP_DATA_CLOUD_SQL_CONNECTOR_EVIDENCE
                  value: cloud-sql-auth-proxy-sidecar
                - name: ODP_DATA_STATUS_MAPPING_PATH
                  value: /var/run/oday/status/status_mapping.json
                - name: ODP_TERMINATION_RECEIPT_PATH
                  value: /var/run/oday/termination.log
              resources:
                requests:
                  cpu: 100m
                  memory: 1Gi
                limits:
                  cpu: "2"
                  memory: 4Gi
              securityContext:
                allowPrivilegeEscalation: false
                capabilities:
                  drop: ["ALL"]
                readOnlyRootFilesystem: true
                runAsNonRoot: true
              terminationMessagePath: /var/run/oday/termination.log
              terminationMessagePolicy: File
              volumeMounts:
                - name: runtime
                  mountPath: /var/run/oday
                - name: state
                  mountPath: /var/lib/oday
                - name: status-mapping
                  mountPath: /var/run/oday/status
                  readOnly: true
          volumes:
            - name: proxy-tmp
              emptyDir: {}
            - name: runtime
              emptyDir:
                sizeLimit: 16Mi
            - name: state
              emptyDir:
                sizeLimit: 4Gi
            - name: status-mapping
              secret:
                secretName: oday-data-platform-status-mapping
                items:
                  - key: status_mapping.json
                    path: status_mapping.json
---
apiVersion: batch/v1
kind: Job
metadata:
  name: oday-data-platform-orders-history-__RELEASE_SHORT__
  namespace: oday-dev
  labels:
    app.kubernetes.io/name: oday-data-platform
    app.kubernetes.io/component: orders-history
    app.kubernetes.io/version: "__RELEASE_SHA__"
  annotations:
    oday.plus/release-sha: "__RELEASE_SHA__"
    oday.plus/image-reference: "__DATA_IMAGE__"
    oday.plus/requires-migration-receipt: "true"
    oday.plus/manual-only: "true"
    oday.plus/hard-limit: "62-days,one-day-partitions,max-250000-per-partition"
    oday.plus/execution-order: "20-orders-history"
spec:
  suspend: true
  backoffLimit: 1
  activeDeadlineSeconds: 14400
  ttlSecondsAfterFinished: 604800
  template:
    metadata:
      labels:
        app.kubernetes.io/name: oday-data-platform
        app.kubernetes.io/component: orders-history
        app.kubernetes.io/version: "__RELEASE_SHA__"
      annotations:
        oday.plus/release-sha: "__RELEASE_SHA__"
        oday.plus/image-reference: "__DATA_IMAGE__"
        oday.plus/requires-migration-receipt: "true"
    spec:
      serviceAccountName: oday-data-platform
      automountServiceAccountToken: true
      restartPolicy: Never
      terminationGracePeriodSeconds: 30
      securityContext:
        runAsNonRoot: true
        runAsUser: 65532
        runAsGroup: 65532
        fsGroup: 65532
        seccompProfile:
          type: RuntimeDefault
      initContainers:
        - name: cloud-sql-auth-proxy
          image: "__CLOUD_SQL_PROXY_IMAGE__"
          imagePullPolicy: IfNotPresent
          restartPolicy: Always
          args:
            - "--structured-logs"
            - "--address=0.0.0.0"
            - "--port=5432"
            - "__CLOUD_SQL_INSTANCE__"
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop: ["ALL"]
            readOnlyRootFilesystem: true
            runAsNonRoot: true
          resources:
            requests: {cpu: 25m, memory: 64Mi}
            limits: {cpu: 500m, memory: 512Mi}
          startupProbe:
            tcpSocket: {port: 5432}
            failureThreshold: 30
            periodSeconds: 2
          volumeMounts:
            - {name: proxy-tmp, mountPath: /tmp}
      containers:
        - name: orders-history
          image: "__DATA_IMAGE__"
          imagePullPolicy: IfNotPresent
          args: ["orders-history"]
          env:
            - {name: ODP_RELEASE_SHA, value: "__RELEASE_SHA__"}
            - {name: ODP_IMAGE_REFERENCE, value: "__DATA_IMAGE__"}
            - {name: ODP_DATA_ENV, value: production}
            - {name: ODP_DATA_MONGO_DATABASE, value: fongniao_prod}
            - name: ODP_DATA_MONGO_URI
              valueFrom:
                secretKeyRef:
                  name: oday-data-platform-runtime
                  key: mongodb-uri
            - {name: ODP_POSTGRES_HOST, value: "127.0.0.1"}
            - {name: ODP_POSTGRES_PORT, value: "5432"}
            - {name: ODP_POSTGRES_USER, value: "__POSTGRES_USER__"}
            - {name: ODP_POSTGRES_DATABASE, value: "__POSTGRES_DATABASE__"}
            - name: ODP_POSTGRES_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: oday-data-platform-runtime
                  key: postgres-password
            - {name: ODP_DATA_RAW_SCHEMA, value: fongniao_raw}
            - {name: ODP_DATA_CONTROL_SCHEMA, value: data_plane}
            - {name: ODP_DATA_BATCH_SIZE, value: "5000"}
            - {name: ODP_DATA_MAX_RECORDS_PER_RUN, value: "250000"}
            - {name: ODP_DATA_CLOUD_SQL_PROXY, value: "true"}
            - name: ODP_DATA_CLOUD_SQL_INSTANCE
              value: "__CLOUD_SQL_INSTANCE__"
            - name: ODP_DATA_CLOUD_SQL_CONNECTOR_EVIDENCE
              value: cloud-sql-auth-proxy-sidecar
            - name: ODP_DATA_STATUS_MAPPING_PATH
              value: /var/run/oday/status/status_mapping.json
            - name: ODP_ORDERS_HISTORY_START
              value: "__ORDERS_HISTORY_START__"
            - name: ODP_ORDERS_HISTORY_END
              value: "__ORDERS_HISTORY_END__"
            - name: ODP_TERMINATION_RECEIPT_PATH
              value: /var/run/oday/termination.log
          resources:
            requests: {cpu: 100m, memory: 1Gi}
            limits: {cpu: "2", memory: 4Gi}
          securityContext:
            allowPrivilegeEscalation: false
            capabilities: {drop: ["ALL"]}
            readOnlyRootFilesystem: true
            runAsNonRoot: true
          terminationMessagePath: /var/run/oday/termination.log
          terminationMessagePolicy: File
          volumeMounts:
            - {name: runtime, mountPath: /var/run/oday}
            - {name: state, mountPath: /var/lib/oday}
            - name: status-mapping
              mountPath: /var/run/oday/status
              readOnly: true
      volumes:
        - {name: proxy-tmp, emptyDir: {}}
        - name: runtime
          emptyDir: {sizeLimit: 16Mi}
        - name: state
          emptyDir: {sizeLimit: 4Gi}
        - name: status-mapping
          secret:
            secretName: oday-data-platform-status-mapping
            items:
              - {key: status_mapping.json, path: status_mapping.json}
---
apiVersion: batch/v1
kind: Job
metadata:
  name: oday-data-platform-trade-manual-__RELEASE_SHORT__
  namespace: oday-dev
  labels:
    app.kubernetes.io/name: oday-data-platform
    app.kubernetes.io/component: trade-manual
    app.kubernetes.io/version: "__RELEASE_SHA__"
  annotations:
    oday.plus/release-sha: "__RELEASE_SHA__"
    oday.plus/image-reference: "__DATA_IMAGE__"
    oday.plus/requires-migration-receipt: "true"
    oday.plus/manual-only: "true"
    oday.plus/hard-limit: "one-day,max-100000"
spec:
  suspend: true
  backoffLimit: 0
  activeDeadlineSeconds: 7200
  ttlSecondsAfterFinished: 604800
  template:
    metadata:
      labels:
        app.kubernetes.io/name: oday-data-platform
        app.kubernetes.io/component: trade-manual
        app.kubernetes.io/version: "__RELEASE_SHA__"
      annotations:
        oday.plus/release-sha: "__RELEASE_SHA__"
        oday.plus/image-reference: "__DATA_IMAGE__"
        oday.plus/requires-migration-receipt: "true"
    spec:
      serviceAccountName: oday-data-platform
      automountServiceAccountToken: true
      restartPolicy: Never
      terminationGracePeriodSeconds: 30
      securityContext:
        runAsNonRoot: true
        runAsUser: 65532
        runAsGroup: 65532
        fsGroup: 65532
        seccompProfile:
          type: RuntimeDefault
      initContainers:
        - name: cloud-sql-auth-proxy
          image: "__CLOUD_SQL_PROXY_IMAGE__"
          imagePullPolicy: IfNotPresent
          restartPolicy: Always
          args:
            - "--structured-logs"
            - "--address=0.0.0.0"
            - "--port=5432"
            - "__CLOUD_SQL_INSTANCE__"
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop: ["ALL"]
            readOnlyRootFilesystem: true
            runAsNonRoot: true
          resources:
            requests: {cpu: 25m, memory: 64Mi}
            limits: {cpu: 500m, memory: 512Mi}
          startupProbe:
            tcpSocket: {port: 5432}
            failureThreshold: 30
            periodSeconds: 2
          volumeMounts:
            - {name: proxy-tmp, mountPath: /tmp}
      containers:
        - name: trade-manual
          image: "__DATA_IMAGE__"
          imagePullPolicy: IfNotPresent
          args: ["trade"]
          env:
            - {name: ODP_RELEASE_SHA, value: "__RELEASE_SHA__"}
            - {name: ODP_IMAGE_REFERENCE, value: "__DATA_IMAGE__"}
            - {name: ODP_DATA_ENV, value: production}
            - {name: ODP_DATA_MONGO_DATABASE, value: fongniao_prod}
            - name: ODP_DATA_MONGO_URI
              valueFrom:
                secretKeyRef: {name: oday-data-platform-runtime, key: mongodb-uri}
            - {name: ODP_POSTGRES_HOST, value: "127.0.0.1"}
            - {name: ODP_POSTGRES_PORT, value: "5432"}
            - {name: ODP_POSTGRES_USER, value: "__POSTGRES_USER__"}
            - {name: ODP_POSTGRES_DATABASE, value: "__POSTGRES_DATABASE__"}
            - name: ODP_POSTGRES_PASSWORD
              valueFrom:
                secretKeyRef: {name: oday-data-platform-runtime, key: postgres-password}
            - {name: ODP_DATA_RAW_SCHEMA, value: fongniao_raw}
            - {name: ODP_DATA_CONTROL_SCHEMA, value: data_plane}
            - {name: ODP_DATA_BATCH_SIZE, value: "5000"}
            - {name: ODP_DATA_MAX_RECORDS_PER_RUN, value: "100000"}
            - {name: ODP_DATA_CLOUD_SQL_PROXY, value: "true"}
            - {name: ODP_DATA_CLOUD_SQL_INSTANCE, value: "__CLOUD_SQL_INSTANCE__"}
            - {name: ODP_DATA_CLOUD_SQL_CONNECTOR_EVIDENCE, value: cloud-sql-auth-proxy-sidecar}
            - {name: ODP_DATA_STATUS_MAPPING_PATH, value: /var/run/oday/status/status_mapping.json}
            - {name: ODP_MANUAL_START, value: "__MANUAL_START__"}
            - {name: ODP_MANUAL_END, value: "__MANUAL_END__"}
            - {name: ODP_TERMINATION_RECEIPT_PATH, value: /var/run/oday/termination.log}
          resources:
            requests: {cpu: 100m, memory: 1Gi}
            limits: {cpu: "2", memory: 4Gi}
          securityContext:
            allowPrivilegeEscalation: false
            capabilities: {drop: ["ALL"]}
            readOnlyRootFilesystem: true
            runAsNonRoot: true
          terminationMessagePath: /var/run/oday/termination.log
          terminationMessagePolicy: File
          volumeMounts:
            - {name: runtime, mountPath: /var/run/oday}
            - {name: state, mountPath: /var/lib/oday}
            - {name: status-mapping, mountPath: /var/run/oday/status, readOnly: true}
      volumes:
        - name: proxy-tmp
          emptyDir: {}
        - name: runtime
          emptyDir: {sizeLimit: 16Mi}
        - name: state
          emptyDir: {sizeLimit: 4Gi}
        - name: status-mapping
          secret:
            secretName: oday-data-platform-status-mapping
            items:
              - {key: status_mapping.json, path: status_mapping.json}
---
apiVersion: batch/v1
kind: Job
metadata:
  name: oday-data-platform-device-log-manual-__RELEASE_SHORT__
  namespace: oday-dev
  labels:
    app.kubernetes.io/name: oday-data-platform
    app.kubernetes.io/component: device-log-manual
    app.kubernetes.io/version: "__RELEASE_SHA__"
  annotations:
    oday.plus/release-sha: "__RELEASE_SHA__"
    oday.plus/image-reference: "__DATA_IMAGE__"
    oday.plus/requires-migration-receipt: "true"
    oday.plus/manual-only: "true"
    oday.plus/hard-limit: "one-day,max-100000"
spec:
  suspend: true
  backoffLimit: 0
  activeDeadlineSeconds: 7200
  ttlSecondsAfterFinished: 604800
  template:
    metadata:
      labels:
        app.kubernetes.io/name: oday-data-platform
        app.kubernetes.io/component: device-log-manual
        app.kubernetes.io/version: "__RELEASE_SHA__"
      annotations:
        oday.plus/release-sha: "__RELEASE_SHA__"
        oday.plus/image-reference: "__DATA_IMAGE__"
        oday.plus/requires-migration-receipt: "true"
    spec:
      serviceAccountName: oday-data-platform
      automountServiceAccountToken: true
      restartPolicy: Never
      terminationGracePeriodSeconds: 30
      securityContext:
        runAsNonRoot: true
        runAsUser: 65532
        runAsGroup: 65532
        fsGroup: 65532
        seccompProfile:
          type: RuntimeDefault
      initContainers:
        - name: cloud-sql-auth-proxy
          image: "__CLOUD_SQL_PROXY_IMAGE__"
          imagePullPolicy: IfNotPresent
          restartPolicy: Always
          args:
            - "--structured-logs"
            - "--address=0.0.0.0"
            - "--port=5432"
            - "__CLOUD_SQL_INSTANCE__"
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop: ["ALL"]
            readOnlyRootFilesystem: true
            runAsNonRoot: true
          resources:
            requests: {cpu: 25m, memory: 64Mi}
            limits: {cpu: 500m, memory: 512Mi}
          startupProbe:
            tcpSocket: {port: 5432}
            failureThreshold: 30
            periodSeconds: 2
          volumeMounts:
            - {name: proxy-tmp, mountPath: /tmp}
      containers:
        - name: device-log-manual
          image: "__DATA_IMAGE__"
          imagePullPolicy: IfNotPresent
          args: ["device-log"]
          env:
            - {name: ODP_RELEASE_SHA, value: "__RELEASE_SHA__"}
            - {name: ODP_IMAGE_REFERENCE, value: "__DATA_IMAGE__"}
            - {name: ODP_DATA_ENV, value: production}
            - {name: ODP_DATA_MONGO_DATABASE, value: fongniao_prod}
            - name: ODP_DATA_MONGO_URI
              valueFrom:
                secretKeyRef: {name: oday-data-platform-runtime, key: mongodb-uri}
            - {name: ODP_POSTGRES_HOST, value: "127.0.0.1"}
            - {name: ODP_POSTGRES_PORT, value: "5432"}
            - {name: ODP_POSTGRES_USER, value: "__POSTGRES_USER__"}
            - {name: ODP_POSTGRES_DATABASE, value: "__POSTGRES_DATABASE__"}
            - name: ODP_POSTGRES_PASSWORD
              valueFrom:
                secretKeyRef: {name: oday-data-platform-runtime, key: postgres-password}
            - {name: ODP_DATA_RAW_SCHEMA, value: fongniao_raw}
            - {name: ODP_DATA_CONTROL_SCHEMA, value: data_plane}
            - {name: ODP_DATA_BATCH_SIZE, value: "5000"}
            - {name: ODP_DATA_MAX_RECORDS_PER_RUN, value: "100000"}
            - {name: ODP_DATA_CLOUD_SQL_PROXY, value: "true"}
            - {name: ODP_DATA_CLOUD_SQL_INSTANCE, value: "__CLOUD_SQL_INSTANCE__"}
            - {name: ODP_DATA_CLOUD_SQL_CONNECTOR_EVIDENCE, value: cloud-sql-auth-proxy-sidecar}
            - {name: ODP_DATA_STATUS_MAPPING_PATH, value: /var/run/oday/status/status_mapping.json}
            - {name: ODP_MANUAL_START, value: "__MANUAL_START__"}
            - {name: ODP_MANUAL_END, value: "__MANUAL_END__"}
            - {name: ODP_TERMINATION_RECEIPT_PATH, value: /var/run/oday/termination.log}
          resources:
            requests: {cpu: 100m, memory: 1Gi}
            limits: {cpu: "2", memory: 4Gi}
          securityContext:
            allowPrivilegeEscalation: false
            capabilities: {drop: ["ALL"]}
            readOnlyRootFilesystem: true
            runAsNonRoot: true
          terminationMessagePath: /var/run/oday/termination.log
          terminationMessagePolicy: File
          volumeMounts:
            - {name: runtime, mountPath: /var/run/oday}
            - {name: state, mountPath: /var/lib/oday}
            - {name: status-mapping, mountPath: /var/run/oday/status, readOnly: true}
      volumes:
        - name: proxy-tmp
          emptyDir: {}
        - name: runtime
          emptyDir: {sizeLimit: 16Mi}
        - name: state
          emptyDir: {sizeLimit: 4Gi}
        - name: status-mapping
          secret:
            secretName: oday-data-platform-status-mapping
            items:
              - {key: status_mapping.json, path: status_mapping.json}
