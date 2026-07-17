package recon

import (
	"strings"
	"testing"

	"github.com/windrose-ai/usage-service/internal/domain"
)

func TestComputeLLMThreshold(t *testing.T) {
	metered := map[string]float64{domain.MeterLLMInputTokens: 1_000_000, domain.MeterQueryBytesScanned: 1000}
	billed := map[string]float64{domain.MeterLLMInputTokens: 1_080_000, domain.MeterQueryBytesScanned: 1050}
	lines, blocking := Compute(metered, billed)
	if !blocking {
		t.Fatal("expected blocking: 8% LLM variance > 5%")
	}
	found := false
	for _, l := range lines {
		if l.MeterKey == domain.MeterLLMInputTokens {
			found = true
			if !l.Blocking {
				t.Fatal("LLM meter should be blocking")
			}
		}
		if l.MeterKey == domain.MeterQueryBytesScanned && l.Blocking {
			t.Fatal("5% infra variance should not block (10% threshold)")
		}
	}
	if !found {
		t.Fatal("llm meter missing")
	}
}

func TestParseBillCSV(t *testing.T) {
	csv := "meter_key,billed_quantity\nllm_input_tokens,1080000\napi_calls,42\n"
	m, err := ParseBillCSV(strings.NewReader(csv))
	if err != nil {
		t.Fatal(err)
	}
	if m[domain.MeterLLMInputTokens] != 1_080_000 {
		t.Fatalf("llm=%v", m[domain.MeterLLMInputTokens])
	}
	if m[domain.MeterAPICalls] != 42 {
		t.Fatalf("api=%v", m[domain.MeterAPICalls])
	}
}
