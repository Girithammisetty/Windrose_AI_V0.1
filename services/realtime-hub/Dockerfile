# realtime-hub — pure-Go static build on a distroless static base
# (MASTER-FR-072). No cgo dependencies (SSE/WS/Kafka/Redis/pgx are all pure Go).
FROM golang:1.26-bookworm AS build
WORKDIR /src
COPY go.work.sum ./
COPY libs/go-common ./libs/go-common
COPY services/realtime-hub ./services/realtime-hub
RUN go work init ./libs/go-common ./services/realtime-hub
WORKDIR /src/services/realtime-hub
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/realtime-hub ./cmd/server

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/realtime-hub /usr/bin/realtime-hub
ENV LISTEN_ADDR=:8080
EXPOSE 8080
USER nonroot
ENTRYPOINT ["/usr/bin/realtime-hub"]
