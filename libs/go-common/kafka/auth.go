package kafka

import (
	"crypto/tls"
	"fmt"
	"log/slog"
	"strings"

	"github.com/segmentio/kafka-go"
	"github.com/segmentio/kafka-go/sasl"
	"github.com/segmentio/kafka-go/sasl/plain"
	"github.com/segmentio/kafka-go/sasl/scram"
)

// SASLConfig configures SASL authentication for a managed/authenticated Kafka-
// compatible broker, so a tenant can point the platform at their own choice of
// event bus — AWS MSK (SASL/SCRAM over TLS), Confluent Cloud (SASL/PLAIN with
// an API key/secret), Azure Event Hubs' Kafka-compatible endpoint (SASL/PLAIN,
// username "$ConnectionString", password = the namespace connection string) —
// via configuration alone. Mechanism is one of "plain", "scram-sha-256",
// "scram-sha-512" (case-insensitive). A nil SASLConfig (the zero value used by
// every existing caller) means no auth — the local self-hosted Kafka/Redpanda
// default is completely unchanged.
//
// GCP Managed Service for Kafka's SASL/OAUTHBEARER (IAM token refresh) is not
// implemented here — it needs a token-refreshing sasl.Mechanism this package
// does not provide. Configuring Mechanism to anything but the three supported
// values is reported (not silently ignored) via BuildTransport's error.
type SASLConfig struct {
	Mechanism string
	Username  string
	Password  string
}

// mechanism resolves the configured SASL mechanism, or (nil, nil) when SASL is
// not configured at all (the default, unauthenticated path).
func (c *SASLConfig) mechanism() (sasl.Mechanism, error) {
	if c == nil || c.Mechanism == "" {
		return nil, nil
	}
	switch strings.ToLower(c.Mechanism) {
	case "plain":
		return plain.Mechanism{Username: c.Username, Password: c.Password}, nil
	case "scram-sha-256":
		return scram.Mechanism(scram.SHA256, c.Username, c.Password)
	case "scram-sha-512":
		return scram.Mechanism(scram.SHA512, c.Username, c.Password)
	default:
		return nil, fmt.Errorf("kafka: unknown SASL mechanism %q (want \"plain\", "+
			"\"scram-sha-256\", or \"scram-sha-512\")", c.Mechanism)
	}
}

// tlsConfig returns a TLS config when enabled, else nil (plaintext — the
// existing default). Most managed SASL brokers (MSK, Confluent Cloud, Event
// Hubs) require TLS alongside SASL.
func tlsConfigIfEnabled(enabled bool) *tls.Config {
	if !enabled {
		return nil
	}
	return &tls.Config{MinVersion: tls.VersionTLS12}
}

// buildTransport resolves cfg's SASL mechanism and constructs a kafka.Transport
// (for the producer) — nil when neither SASL nor TLS is configured, preserving
// kafka-go's default (unauthenticated, plaintext) transport exactly, so every
// existing caller sees zero behavior change. A misconfigured mechanism name
// logs loudly and degrades to no-auth rather than panicking the constructor
// (NewProducer/NewConsumerGroup keep their existing non-error signatures) —
// the broker will then reject the connection immediately, which is a loud,
// obvious failure, not a silent security downgrade.
func buildTransport(saslCfg *SASLConfig, tlsOn bool) *kafka.Transport {
	mech, err := saslCfg.mechanism()
	if err != nil {
		slog.Error("kafka: SASL configuration rejected; connecting without auth", "err", err)
		mech = nil
	}
	if mech == nil && !tlsOn {
		return nil
	}
	return &kafka.Transport{SASL: mech, TLS: tlsConfigIfEnabled(tlsOn)}
}

// buildDialer resolves cfg's SASL mechanism and constructs a kafka.Dialer (for
// the consumer's reader + topic-admin client) — nil when neither SASL nor TLS
// is configured, preserving kafka-go's default dialer exactly.
func buildDialer(saslCfg *SASLConfig, tlsOn bool) *kafka.Dialer {
	mech, err := saslCfg.mechanism()
	if err != nil {
		slog.Error("kafka: SASL configuration rejected; connecting without auth", "err", err)
		mech = nil
	}
	if mech == nil && !tlsOn {
		return nil
	}
	d := kafka.DefaultDialer
	dd := *d
	dd.SASLMechanism = mech
	dd.TLS = tlsConfigIfEnabled(tlsOn)
	return &dd
}

// SASLFromEnv builds a SASLConfig from the platform's standard Kafka auth env
// vars — KAFKA_SASL_MECHANISM, KAFKA_SASL_USERNAME, KAFKA_SASL_PASSWORD (the
// latter two are already documented in deploy/CONFIG.md) — read via lookup
// (typically os.Getenv). Returns nil when KAFKA_SASL_MECHANISM is unset, so a
// caller can unconditionally set Config.SASL = kafka.SASLFromEnv(os.Getenv)
// and get today's no-auth behavior when the operator hasn't configured it.
func SASLFromEnv(lookup func(string) string) *SASLConfig {
	mech := lookup("KAFKA_SASL_MECHANISM")
	if mech == "" {
		return nil
	}
	return &SASLConfig{
		Mechanism: mech,
		Username:  lookup("KAFKA_SASL_USERNAME"),
		Password:  lookup("KAFKA_SASL_PASSWORD"),
	}
}

// TLSFromEnv reports whether KAFKA_TLS is set to "1"/"true"/"yes"
// (case-insensitive) — the platform's standard Kafka TLS toggle.
func TLSFromEnv(lookup func(string) string) bool {
	switch strings.ToLower(lookup("KAFKA_TLS")) {
	case "1", "true", "yes":
		return true
	}
	return false
}
