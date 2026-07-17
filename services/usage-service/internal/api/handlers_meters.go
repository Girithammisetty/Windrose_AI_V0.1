package api

import "net/http"

// handleListMeters returns the seeded meter catalog (USG-FR-003).
func (s *Server) handleListMeters(w http.ResponseWriter, r *http.Request) {
	meters, err := s.Store.ListMeters(r.Context())
	if err != nil {
		writeErr(w, r, err)
		return
	}
	writePage(w, meters, "", false)
}
