# chart-service — pure-Go build on a static distroless base (MASTER-FR-072).
FROM golang:1.26-bookworm AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/chart-service ./cmd/server

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/chart-service /usr/bin/chart-service
ENV EXPORT_ROOT=/var/lib/chart-service/exports
USER nonroot
ENTRYPOINT ["/usr/bin/chart-service"]
