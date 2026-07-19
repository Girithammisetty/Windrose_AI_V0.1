# case-service — pure-Go build on a distroless static base (MASTER-FR-072).
FROM golang:1.26-bookworm AS build
WORKDIR /src
COPY go.work.sum ./
COPY libs/go-common ./libs/go-common
COPY services/case-service ./services/case-service
RUN go work init ./libs/go-common ./services/case-service
WORKDIR /src/services/case-service
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/case-service ./cmd/server

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/case-service /usr/bin/case-service
ENV SNAPSHOT_ROOT=/var/lib/case-service/snapshots
USER nonroot
ENTRYPOINT ["/usr/bin/case-service"]
