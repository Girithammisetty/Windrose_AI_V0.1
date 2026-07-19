# notification-service — CGO-free build on a distroless static base
# (MASTER-FR-072).
FROM golang:1.26-bookworm AS build
WORKDIR /src
COPY go.work.sum ./
COPY libs/go-common ./libs/go-common
COPY services/notification-service ./services/notification-service
RUN go work init ./libs/go-common ./services/notification-service
WORKDIR /src/services/notification-service
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/notification-service ./cmd/server

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/notification-service /usr/bin/notification-service
EXPOSE 8087
USER nonroot
ENTRYPOINT ["/usr/bin/notification-service"]
