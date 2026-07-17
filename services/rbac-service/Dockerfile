# Build
FROM golang:1.26 AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /out/server ./cmd/server \
 && CGO_ENABLED=0 go build -trimpath -ldflags='-s -w' -o /out/rebuild ./cmd/rebuild

# Run (distroless, non-root)
FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/server /server
COPY --from=build /out/rebuild /rebuild
COPY policy /policy
EXPOSE 8080
USER nonroot
ENTRYPOINT ["/server"]
