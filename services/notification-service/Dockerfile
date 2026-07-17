# notification-service — CGO-free build on a distroless static base
# (MASTER-FR-072).
FROM golang:1.26-bookworm AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/notification-service ./cmd/server

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/notification-service /usr/bin/notification-service
EXPOSE 8087
USER nonroot
ENTRYPOINT ["/usr/bin/notification-service"]
