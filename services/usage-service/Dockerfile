# usage-service — pure-Go build on a static distroless base (MASTER-FR-072).
# Build context is the repo root so the go.work / libs/go-common replace
# resolves (docker build -f services/usage-service/Dockerfile .).
FROM golang:1.26-bookworm AS build
WORKDIR /src
COPY go.work go.work.sum ./
COPY libs/go-common ./libs/go-common
COPY services/usage-service ./services/usage-service
WORKDIR /src/services/usage-service
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/usage-service ./cmd/server

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/usage-service /usr/bin/usage-service
USER nonroot
EXPOSE 8080
ENTRYPOINT ["/usr/bin/usage-service"]
