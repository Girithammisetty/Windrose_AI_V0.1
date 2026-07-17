// Package embed computes real semantic embeddings of tool descriptions for
// discovery (TPL-FR-020/021). The Embedder is a REAL client of the local Ollama
// OpenAI-compatible embeddings API (nomic-embed-text, 768-dim) — not a hash
// fake. Embeddings are computed at publish time and stored in pgvector; the
// discovery search ranks tools by cosine similarity against the same model.
package embed

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"time"
)

// ModelNomic is the embedding model served by Ollama in the dev stack.
const ModelNomic = "nomic-embed-text"

// Dim is the nomic-embed-text output dimensionality (matches vector(768)).
const Dim = 768

// Embedder produces embeddings for text.
type Embedder interface {
	Embed(ctx context.Context, text string) ([]float32, error)
	Model() string
}

// Ollama is the real embeddings client (OpenAI-compatible /v1/embeddings).
type Ollama struct {
	BaseURL string // e.g. http://localhost:11434/v1
	ModelID string
	client  *http.Client
}

// NewOllama builds a client for baseURL (the /v1 root) and model.
func NewOllama(baseURL, model string) *Ollama {
	if model == "" {
		model = ModelNomic
	}
	return &Ollama{
		BaseURL: baseURL,
		ModelID: model,
		client:  &http.Client{Timeout: 30 * time.Second},
	}
}

// Model returns the model version string stored per row (TPL-FR-021).
func (o *Ollama) Model() string { return o.ModelID }

type embedReq struct {
	Model string `json:"model"`
	Input string `json:"input"`
}

type embedResp struct {
	Data []struct {
		Embedding []float32 `json:"embedding"`
	} `json:"data"`
	Model string `json:"model"`
}

// Embed returns the real embedding vector for text.
func (o *Ollama) Embed(ctx context.Context, text string) ([]float32, error) {
	body, _ := json.Marshal(embedReq{Model: o.ModelID, Input: text})
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, o.BaseURL+"/embeddings", bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := o.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("ollama embed: %w", err)
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("ollama embed: status %d", resp.StatusCode)
	}
	var out embedResp
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		return nil, err
	}
	if len(out.Data) == 0 || len(out.Data[0].Embedding) == 0 {
		return nil, fmt.Errorf("ollama embed: empty embedding")
	}
	return out.Data[0].Embedding, nil
}

// Cosine returns cosine similarity of two equal-length vectors (used by the
// unit-tier discovery ranking and any non-pgvector fallback).
func Cosine(a, b []float32) float64 {
	if len(a) != len(b) || len(a) == 0 {
		return 0
	}
	var dot, na, nb float64
	for i := range a {
		dot += float64(a[i]) * float64(b[i])
		na += float64(a[i]) * float64(a[i])
		nb += float64(b[i]) * float64(b[i])
	}
	if na == 0 || nb == 0 {
		return 0
	}
	return dot / (sqrt(na) * sqrt(nb))
}

func sqrt(x float64) float64 {
	if x <= 0 {
		return 0
	}
	// Newton's method — avoids importing math for one call in a hot helper.
	z := x
	for i := 0; i < 40; i++ {
		z -= (z*z - x) / (2 * z)
	}
	return z
}

// EmbeddingText is the canonical text embedded for a tool version: the semantic
// description plus example descriptions (TPL-FR-020 "over semantic_description +
// examples").
func EmbeddingText(description string, exampleDescs []string) string {
	text := description
	for _, e := range exampleDescs {
		if e != "" {
			text += "\n" + e
		}
	}
	return text
}
