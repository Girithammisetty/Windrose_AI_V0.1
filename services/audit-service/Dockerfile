# audit-service — static build on a distroless base (MASTER-FR-072). No cgo:
# the ClickHouse, MinIO, Kafka and Parquet clients are all pure Go.
FROM golang:1.26-bookworm AS build
WORKDIR /src
COPY go.work.sum ./
COPY libs/go-common ./libs/go-common
COPY services/audit-service ./services/audit-service
RUN go work init ./libs/go-common ./services/audit-service
WORKDIR /src/services/audit-service
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/audit-service ./cmd/server

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/audit-service /usr/bin/audit-service
USER nonroot
ENTRYPOINT ["/usr/bin/audit-service"]
