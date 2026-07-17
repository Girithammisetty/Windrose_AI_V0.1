package chain

import (
	"testing"
	"time"

	"github.com/google/uuid"

	"github.com/windrose-ai/audit-service/internal/domain"
)

// buildChain constructs a valid chain of n records for (tenant, date).
func buildChain(tenant uuid.UUID, date string, n int) ([]domain.Record, string) {
	prev := domain.GenesisHash(tenant, date)
	base := time.Date(2026, 7, 8, 0, 0, 0, 0, time.UTC)
	var rows []domain.Record
	for i := 0; i < n; i++ {
		id := uuid.New()
		occ := base.Add(time.Duration(i) * time.Second)
		digest := domain.SHA256Hex([]byte{byte(i)})
		h := domain.ChainHash(prev, id, digest, occ)
		rows = append(rows, domain.Record{
			EventID: id, TenantID: tenant, OccurredAt: occ,
			PayloadDigest: digest, ChainDate: date, ChainSeq: uint64(i + 1), ChainHash: h,
		})
		prev = h
	}
	return rows, prev
}

func TestVerifyValidChain(t *testing.T) {
	tenant := uuid.New()
	rows, head := buildChain(tenant, "2026-07-08", 10)
	res := Verify(rows, tenant, "2026-07-08", head)
	if !res.Valid || !res.ManifestMatch || res.EventsChecked != 10 {
		t.Fatalf("valid chain failed verification: %+v", res)
	}
}

func TestVerifyDetectsTamper(t *testing.T) {
	tenant := uuid.New()
	rows, head := buildChain(tenant, "2026-07-08", 10)
	// Tamper: mutate the stored payload_digest of row index 4 (seq 5) — as if an
	// attacker altered a persisted record but left the chain hashes in place.
	rows[4].PayloadDigest = domain.SHA256Hex([]byte("tampered"))
	res := Verify(rows, tenant, "2026-07-08", head)
	if res.Valid {
		t.Fatal("tamper not detected")
	}
	if res.FirstMismatch == nil || *res.FirstMismatch != 5 {
		t.Fatalf("wrong first mismatch seq: %+v", res.FirstMismatch)
	}
}

func TestVerifyDetectsHeadMismatch(t *testing.T) {
	tenant := uuid.New()
	rows, _ := buildChain(tenant, "2026-07-08", 5)
	res := Verify(rows, tenant, "2026-07-08", "deadbeef")
	if res.Valid || res.ManifestMatch {
		t.Fatalf("sealed-head mismatch should invalidate: %+v", res)
	}
}
