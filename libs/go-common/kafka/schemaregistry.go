package kafka

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"time"
)

// SchemaRegistry is a minimal real client for the Confluent-compatible Schema
// Registry API exposed by Redpanda (deploy: localhost:8081). It registers the
// event envelope subject so producers publish under a governed, versioned
// schema (MASTER-FR-030: Avro + Schema Registry, backward-compatible evolution
// enforced in CI). Payloads are encoded as JSON today; the registered schema is
// the contract of record and the subject id travels in a Kafka header.
type SchemaRegistry struct {
	BaseURL string
	client  *http.Client
}

// NewSchemaRegistry builds a client for baseURL (e.g. http://localhost:8081).
func NewSchemaRegistry(baseURL string) *SchemaRegistry {
	return &SchemaRegistry{BaseURL: baseURL, client: &http.Client{Timeout: 5 * time.Second}}
}

// SubjectFor is the standard TopicNameStrategy subject name for a topic's value.
func SubjectFor(topic string) string { return topic + "-value" }

// Register registers schema (an Avro schema JSON document) under subject and
// returns the assigned schema id. Idempotent: re-registering an identical
// schema returns the existing id.
func (s *SchemaRegistry) Register(ctx context.Context, subject, schema string) (int, error) {
	body, _ := json.Marshal(map[string]any{"schema": schema, "schemaType": "AVRO"})
	url := fmt.Sprintf("%s/subjects/%s/versions", s.BaseURL, subject)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return 0, err
	}
	req.Header.Set("Content-Type", "application/vnd.schemaregistry.v1+json")
	resp, err := s.client.Do(req)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		var e struct {
			ErrorCode int    `json:"error_code"`
			Message   string `json:"message"`
		}
		_ = json.NewDecoder(resp.Body).Decode(&e)
		return 0, fmt.Errorf("schema registry register %s: status %d (%d %s)", subject, resp.StatusCode, e.ErrorCode, e.Message)
	}
	var out struct {
		ID int `json:"id"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return 0, err
	}
	return out.ID, nil
}

// LatestID returns the schema id of the latest version registered for subject.
func (s *SchemaRegistry) LatestID(ctx context.Context, subject string) (int, error) {
	url := fmt.Sprintf("%s/subjects/%s/versions/latest", s.BaseURL, subject)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return 0, err
	}
	resp, err := s.client.Do(req)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return 0, fmt.Errorf("schema registry latest %s: status %d", subject, resp.StatusCode)
	}
	var out struct {
		ID int `json:"id"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return 0, err
	}
	return out.ID, nil
}
