# query-service — CGO build (DuckDB engine) on a distroless glibc base
# (MASTER-FR-072).
FROM golang:1.26-bookworm AS build
WORKDIR /src
COPY go.work.sum ./
COPY libs/go-common ./libs/go-common
COPY services/query-service ./services/query-service
RUN go work init ./libs/go-common ./services/query-service
WORKDIR /src/services/query-service
RUN CGO_ENABLED=1 go build -trimpath -ldflags="-s -w" -o /out/query-service ./cmd/server

# distroless/base carries glibc (required by the cgo DuckDB driver);
# static-debian would not.
FROM gcr.io/distroless/base-debian12:nonroot
COPY --from=build /out/query-service /usr/bin/query-service
ENV RESULTS_ROOT=/var/lib/query-service
USER nonroot
ENTRYPOINT ["/usr/bin/query-service"]
