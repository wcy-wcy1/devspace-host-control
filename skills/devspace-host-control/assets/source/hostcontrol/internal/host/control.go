package host

import (
	"crypto/subtle"
	_ "embed"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"strconv"
	"time"
)

//go:embed control.html
var controlHTML string

func (s *Service) StartControl() (string, error) {
	l, e := net.Listen("tcp4", "127.0.0.1:0")
	if e != nil {
		return "", e
	}
	token := randomID() + randomID()
	origin := "http://" + l.Addr().String()
	if e := s.writeInputEndpoint(origin); e != nil {
		l.Close()
		return "", e
	}
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
		if r.Host != l.Addr().String() || r.Header.Get("Origin") != "" && r.Header.Get("Origin") != origin {
			http.Error(w, "forbidden", 403)
			return
		}
		if r.URL.Path == "/" && r.Method == "GET" {
			w.Header().Set("Content-Type", "text/html; charset=utf-8")
			fmt.Fprint(w, controlHTML)
			return
		}
		if r.URL.Path == "/input-relay" && s.input != nil {
			s.mu.Lock()
			blocked := s.revoked
			s.mu.Unlock()
			if blocked {
				http.Error(w, "pairing revoked", 403)
				return
			}
			s.input.local(w, r)
			return
		}
		if r.Method != "POST" || subtle.ConstantTimeCompare([]byte(r.Header.Get("Authorization")), []byte("Bearer "+token)) != 1 {
			http.Error(w, "forbidden", 403)
			return
		}
		switch r.URL.Path {
		case "/enable":
			m, e := strconv.Atoi(r.URL.Query().Get("minutes"))
			if e != nil || s.Enable(m) != nil {
				http.Error(w, "invalid duration or revoked pairing", 400)
				return
			}
		case "/pause":
			s.Pause(false)
		case "/revoke":
			if e := s.Pause(true); e != nil {
				http.Error(w, e.Error(), 500)
				return
			}
		case "/status":
		case "/workspace-access":
			if e := s.setWorkspaceAccess(r.URL.Query().Get("workspace"), r.URL.Query().Get("access")); e != nil {
				http.Error(w, e.Error(), http.StatusConflict)
				return
			}
		default:
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(s.localStatus())
	})
	server := &http.Server{Handler: handler, ReadHeaderTimeout: 3 * time.Second, ReadTimeout: 5 * time.Second, WriteTimeout: 10 * time.Second, IdleTimeout: 15 * time.Second}
	go server.Serve(l)
	return origin + "/#" + token, nil
}
