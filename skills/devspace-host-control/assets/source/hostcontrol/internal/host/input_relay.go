package host

// Optional opaque text transport. Local panel commands travel to the paired
// Linux receiver over its outbound mTLS connection; this is not an MCP tool.
import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

type inputEnvelope struct {
	Action  string `json:"action,omitempty"`
	Session string `json:"session,omitempty"`
	Ticket  string `json:"ticket,omitempty"`
	Packet  string `json:"packet,omitempty"`
}
type inputItem struct {
	ticket, packet, result string
	claimed                bool
}
type inputRelay struct {
	mu             sync.Mutex
	token, session string
	expires        time.Time
	item           *inputItem
	changed        chan struct{}
}

func newInputRelay() *inputRelay {
	return &inputRelay{token: randomID() + randomID(), changed: make(chan struct{})}
}
func validOperationID(id string) bool {
	if len(id) != 32 {
		return false
	}
	for _, c := range id {
		if !strings.ContainsRune("0123456789abcdef", c) {
			return false
		}
	}
	return true
}
func (b *inputRelay) signal() { close(b.changed); b.changed = make(chan struct{}) }
func (b *inputRelay) close() {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.session = ""
	b.item = nil
	b.signal()
}
func bridgePacket(raw string) bool {
	return len(raw) <= 262144 && (strings.HasPrefix(raw, "HC-DRAFT/2 ") || strings.HasPrefix(raw, "HC-DRAFT/3 "))
}
func relayJSON(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(v)
}
func relayDecode(w http.ResponseWriter, r *http.Request) (inputEnvelope, error) {
	var v inputEnvelope
	d := json.NewDecoder(http.MaxBytesReader(w, r.Body, 300000))
	d.DisallowUnknownFields()
	if e := d.Decode(&v); e != nil {
		return v, e
	}
	if e := d.Decode(new(any)); e != io.EOF {
		return v, fmt.Errorf("one packet required")
	}
	return v, nil
}
func (b *inputRelay) local(w http.ResponseWriter, r *http.Request) {
	if r.Method != "POST" || subtle.ConstantTimeCompare([]byte(r.Header.Get("Authorization")), []byte("Bearer "+b.token)) != 1 {
		http.Error(w, "forbidden", 403)
		return
	}
	v, e := relayDecode(w, r)
	if e != nil || !validOperationID(v.Session) {
		http.Error(w, "invalid relay request", 400)
		return
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	if v.Action == "open" {
		if b.session != "" && b.session != v.Session && time.Now().Before(b.expires) {
			http.Error(w, "another local panel owns this transport", 409)
			return
		}
		if b.session != v.Session {
			b.item = nil
		}
		b.session = v.Session
		b.expires = time.Now().Add(30 * time.Minute)
		b.signal()
		relayJSON(w, map[string]bool{"open": true})
		return
	}
	if v.Session != b.session || !time.Now().Before(b.expires) {
		http.Error(w, "local input session is closed or expired", 409)
		return
	}
	switch v.Action {
	case "send":
		if !bridgePacket(v.Packet) || !validOperationID(v.Ticket) {
			http.Error(w, "invalid packet", 400)
			return
		}
		if b.item != nil && b.item.result == "" {
			http.Error(w, "previous outcome pending; do not retry", 409)
			return
		}
		// Reusing a delivery ticket is never a resend, even after an acknowledgement.
		if b.item != nil && b.item.ticket == v.Ticket {
			http.Error(w, "ticket already consumed", 409)
			return
		}
		b.item = &inputItem{ticket: v.Ticket, packet: v.Packet}
		b.signal()
		relayJSON(w, map[string]bool{"queued": true})
	case "read":
		if b.item == nil || b.item.ticket != v.Ticket {
			http.Error(w, "unknown ticket", 409)
			return
		}
		if b.item.result == "" {
			w.WriteHeader(204)
			return
		}
		relayJSON(w, inputEnvelope{Ticket: v.Ticket, Packet: b.item.result})
	case "close":
		b.session = ""
		b.item = nil
		b.signal()
		relayJSON(w, map[string]bool{"closed": true})
	default:
		http.Error(w, "unknown local action", 400)
	}
}
func (b *inputRelay) exchange(ctx context.Context, v inputEnvelope) inputEnvelope {
	b.mu.Lock()
	if v.Packet != "" && bridgePacket(v.Packet) && b.item != nil && b.item.claimed && b.item.result == "" && v.Session == b.session && v.Ticket == b.item.ticket && time.Now().Before(b.expires) {
		b.item.result = v.Packet
		b.signal()
	}
	b.mu.Unlock()
	deadline := time.NewTimer(20 * time.Second)
	defer deadline.Stop()
	for {
		b.mu.Lock()
		if b.session != "" && time.Now().Before(b.expires) && b.item != nil && !b.item.claimed {
			// Claim before writing HTTP. A disconnect is unknown; never redeliver input.
			b.item.claimed = true
			result := inputEnvelope{Session: b.session, Ticket: b.item.ticket, Packet: b.item.packet}
			b.mu.Unlock()
			return result
		}
		wake := b.changed
		b.mu.Unlock()
		select {
		case <-ctx.Done():
			return inputEnvelope{}
		case <-deadline.C:
			return inputEnvelope{}
		case <-wake:
		}
	}
}
func (s *Service) remoteInput(w http.ResponseWriter, r *http.Request) {
	s.mu.Lock()
	blocked := s.revoked
	s.mu.Unlock()
	if s.input == nil || blocked {
		http.Error(w, "input relay disabled", 403)
		return
	}
	if r.Method != "POST" {
		http.Error(w, "POST required", 405)
		return
	}
	v, e := relayDecode(w, r)
	if e != nil || v.Action != "" || (v.Packet != "" && (!bridgePacket(v.Packet) || !validOperationID(v.Session) || !validOperationID(v.Ticket))) {
		http.Error(w, "invalid exchange", 400)
		return
	}
	relayJSON(w, s.input.exchange(r.Context(), v))
}
func (s *Service) writeInputEndpoint(origin string) error {
	if s.input == nil {
		return nil
	}
	path := s.cfg.InputRelayState
	// The executor chooses a private directory outside authorized file roots.
	// Never write the local-only bearer into logs or return it to the TLS peer.
	if e := protectDir(filepath.Dir(path)); e != nil {
		return e
	}
	data, _ := json.Marshal(map[string]string{"url": origin + "/input-relay", "token": s.input.token})
	data, e := protectKey(data)
	if e != nil {
		return e
	}
	tmp, e := os.CreateTemp(filepath.Dir(path), ".input-relay-*")
	if e != nil {
		return e
	}
	name := tmp.Name()
	defer os.Remove(name)
	if e = tmp.Chmod(0600); e == nil {
		_, e = tmp.Write(data)
	}
	ce := tmp.Close()
	if e != nil {
		return e
	}
	if ce != nil {
		return ce
	}
	return os.Rename(name, path)
}
