# Build
FROM golang:1.26 AS build
WORKDIR /src
COPY go.work.sum ./
COPY libs/go-common ./libs/go-common
COPY services/rbac-service ./services/rbac-service
RUN go work init ./libs/go-common ./services/rbac-service
WORKDIR /src/services/rbac-service
RUN CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /out/server ./cmd/server \
 && CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /out/rebuild ./cmd/rebuild

# Run (distroless, non-root)
FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/server /server
COPY --from=build /out/rebuild /rebuild
COPY services/rbac-service/policy /policy
EXPOSE 8080
USER nonroot
ENTRYPOINT ["/server"]
