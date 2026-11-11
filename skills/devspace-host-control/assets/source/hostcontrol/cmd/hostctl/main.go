package main

import (
	"flag"
	"fmt"
	"github.com/loft-sh/devspace/hostcontrol/internal/host"
	"os"
)

func main() {
	if len(os.Args) < 2 {
		fail(fmt.Errorf("usage: hostctl init|request|approve|fingerprint"))
	}
	f := flag.NewFlagSet(os.Args[1], flag.ExitOnError)
	dir := f.String("dir", "", "credential directory")
	ip := f.String("ip", "", "host Tailscale IP")
	csr := f.String("csr", "", "client CSR")
	out := f.String("out", "", "output certificate")
	cert := f.String("cert", "", "certificate path")
	f.Parse(os.Args[2:])
	var e error
	switch os.Args[1] {
	case "init":
		if *dir == "" {
			fail(fmt.Errorf("--dir is required"))
		}
		e = host.InitPKI(*dir, *ip)
	case "request":
		if *dir == "" {
			fail(fmt.Errorf("--dir is required"))
		}
		e = host.RequestPKI(*dir)
	case "approve":
		if *dir == "" || *csr == "" || *out == "" {
			fail(fmt.Errorf("--dir --csr --out required"))
		}
		e = host.ApprovePKI(*dir, *csr, *out)
	case "fingerprint":
		var s string
		s, e = host.CertificateFingerprint(*cert)
		if e == nil {
			fmt.Println(s)
		}
	default:
		e = fmt.Errorf("unknown command")
	}
	if e != nil {
		fail(e)
	}
}
func fail(e error) { fmt.Fprintln(os.Stderr, e); os.Exit(1) }
