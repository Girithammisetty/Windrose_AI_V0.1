{{/*
Common helpers for the Windrose umbrella chart.
*/}}

{{- define "windrose.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Common labels applied to every object. */}}
{{- define "windrose.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
app.kubernetes.io/part-of: windrose
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{/* Per-service selector labels. Call with a dict: (dict "svc" $svc "root" $). */}}
{{- define "windrose.selectorLabels" -}}
app.kubernetes.io/name: {{ .svc.name }}
app.kubernetes.io/component: service
{{- end -}}

{{/* Fully-qualified image ref for a service. Call with (dict "svc" $svc "root" $). */}}
{{- define "windrose.image" -}}
{{- $g := .root.Values.global -}}
{{- printf "%s/%s:%s" $g.registry .svc.name (default $g.imageTag .svc.imageTag) -}}
{{- end -}}

{{/*
Resolve the migration command for a service.
  - explicit .svc.migrateCommand wins (a JSON/YAML list)
  - else python services default to alembic upgrade head (alembic + migrations
    ship in the image; the distroless entrypoint is overridden by the Job)
  - else empty  => no dedicated Job (Go services self-migrate idempotently on
    boot under MIGRATE_DATABASE_URL; see README).
Returns a JSON array string, or "" when there is no command.
*/}}
{{- define "windrose.migrateCommand" -}}
{{- if .svc.migrateCommand -}}
{{- toJson .svc.migrateCommand -}}
{{- else if eq .svc.language "python" -}}
{{- toJson (list "python" "-m" "alembic" "upgrade" "head") -}}
{{- end -}}
{{- end -}}

{{/* The readiness probe path for a service (default /readyz). */}}
{{- define "windrose.readyPath" -}}
{{- default "/readyz" .svc.health -}}
{{- end -}}

{{/* The liveness probe path for a service (default /healthz). */}}
{{- define "windrose.livePath" -}}
{{- default "/healthz" .svc.liveness -}}
{{- end -}}

{{/* ServiceAccount name used by every workload. */}}
{{- define "windrose.serviceAccountName" -}}
{{- default (printf "%s-sa" (include "windrose.name" .)) .Values.serviceAccount.name -}}
{{- end -}}
