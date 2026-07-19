# Build
FROM golang:1.26 AS build
WORKDIR /src
COPY go.work.sum ./
COPY libs/go-common ./libs/go-common
COPY services/identity-service ./services/identity-service
RUN go work init ./libs/go-common ./services/identity-service
WORKDIR /src/services/identity-service
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/identity-service ./cmd/server

# Run (distroless, non-root — MASTER-FR-072)
FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/identity-service /identity-service
EXPOSE 8080
USER nonroot:nonroot
ENTRYPOINT ["/identity-service"]
