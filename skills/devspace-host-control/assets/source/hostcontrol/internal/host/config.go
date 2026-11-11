package host

import (
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/netip"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const Version = "0.1.0"

type Config struct {
	Listen          string            `json:"listen"`
	PeerIP          string            `json:"peer_ip"`
	CA              string            `json:"ca"`
	Cert            string            `json:"cert"`
	Key             string            `json:"key"`
	PeerFingerprint string            `json:"peer_fingerprint"`
	Roots           map[string]string `json:"roots"`
	Capabilities    []string          `json:"capabilities"`
	SessionMinutes  int               `json:"session_minutes"`
	Audit           string            `json:"audit,omitempty"`
}

type ClientConfig struct {
	Endpoint          string `json:"endpoint"`
	CA                string `json:"ca"`
	Cert              string `json:"cert"`
	Key               string `json:"key"`
	ServerFingerprint string `json:"server_fingerprint"`
}

func ReadJSON(path string, dst any) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	d := json.NewDecoder(io.LimitReader(f, 1<<20))
	d.DisallowUnknownFields()
	if err = d.Decode(dst); err != nil {
		return err
	}
	if err = d.Decode(new(any)); err != io.EOF {
		return fmt.Errorf("configuration must contain one JSON value")
	}
	return nil
}

func IsVPN(ip string) bool {
	a, err := netip.ParseAddr(ip)
	if err != nil || a.Zone() != "" {
		return false
	}
	a = a.Unmap()
	return netip.MustParsePrefix("100.64.0.0/10").Contains(a) || netip.MustParsePrefix("fd7a:115c:a1e0::/48").Contains(a)
}

func validFingerprint(s string) bool {
	if len(s) != 64 {
		return false
	}
	for _, c := range s {
		if !strings.ContainsRune("0123456789abcdef", c) {
			return false
		}
	}
	return true
}

func (c *Config) Validate() error {
	ip, port, err := net.SplitHostPort(c.Listen)
	if err != nil || port == "" || !IsVPN(ip) || !IsVPN(c.PeerIP) {
		return fmt.Errorf("listen and peer_ip must be exact Tailscale addresses (not wildcards, DNS names, or CIDRs)")
	}
	if !validFingerprint(c.PeerFingerprint) {
		return fmt.Errorf("peer_fingerprint must be the approved client certificate SHA256, lowercase hex")
	}
	if c.CA == "" || c.Cert == "" || c.Key == "" {
		return fmt.Errorf("ca, cert and key are required")
	}
	if c.SessionMinutes < 1 || c.SessionMinutes > 480 {
		return fmt.Errorf("session_minutes must be 1..480")
	}
	if len(c.Roots) == 0 {
		return fmt.Errorf("at least one workspace is required")
	}
	for name, path := range c.Roots {
		if name == "" || strings.ContainsAny(name, "/\\:") || !filepath.IsAbs(path) {
			return fmt.Errorf("invalid workspace")
		}
		st, e := os.Stat(path)
		if e != nil || !st.IsDir() {
			return fmt.Errorf("workspace %q must be an existing directory", name)
		}
	}
	if len(c.Capabilities) == 0 {
		return fmt.Errorf("capabilities must be explicit")
	}
	for _, s := range c.Capabilities {
		if s != "files" && s != "exec" && s != "desktop" {
			return fmt.Errorf("unknown capability %q", s)
		}
	}
	return nil
}

func (c *ClientConfig) Validate() error {
	u, err := url.Parse(c.Endpoint)
	if err != nil || u.Scheme != "https" || !IsVPN(u.Hostname()) || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Path != "" && u.Path != "/") {
		return fmt.Errorf("endpoint must be https://<exact Tailscale IP>:port")
	}
	if c.CA == "" || c.Cert == "" || c.Key == "" || !validFingerprint(c.ServerFingerprint) {
		return fmt.Errorf("ca, cert, key and server_fingerprint are required")
	}
	return nil
}

func (c Config) Has(cap string) bool {
	for _, v := range c.Capabilities {
		if v == cap {
			return true
		}
	}
	return false
}

func canonicalIP(s string) string {
	a, e := netip.ParseAddr(s)
	if e != nil {
		return ""
	}
	return a.Unmap().String()
}

func defaultDeadline(minutes int) time.Time {
	return time.Now().Add(time.Duration(minutes) * time.Minute)
}
