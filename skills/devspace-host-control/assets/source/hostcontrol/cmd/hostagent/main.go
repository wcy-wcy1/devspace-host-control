package main

import (
	"flag"
	"fmt"
	"github.com/loft-sh/devspace/hostcontrol/internal/host"
	"os"
	"os/signal"
)

func main() {
	config := flag.String("config", "", "agent JSON configuration")
	check := flag.Bool("check", false, "validate configuration without listening")
	enable := flag.Int("enable-for", 0, "explicitly authorize a session in minutes on local startup")
	flag.Parse()
	var c host.Config
	e := host.ReadJSON(*config, &c)
	if e == nil {
		e = c.Validate()
	}
	if e != nil {
		fail(e)
	}
	if *check {
		fmt.Println("configuration valid")
		return
	}
	s, e := host.NewService(c)
	if e != nil {
		fail(e)
	}
	if *enable != 0 {
		if e = s.Enable(*enable); e != nil {
			fail(e)
		}
	}
	url, e := s.StartControl()
	if e != nil {
		fail(e)
	}
	fmt.Println("Local control (keep this URL private):", url)
	fmt.Println("Default state: paused unless --enable-for was explicitly supplied. Ctrl+C ends this session.")
	stop := make(chan struct{})
	go s.Watch(stop)
	signals := make(chan os.Signal, 1)
	signal.Notify(signals, os.Interrupt)
	go func() { <-signals; s.Pause(false); close(stop); os.Exit(0) }()
	if e = s.Serve(); e != nil {
		fail(e)
	}
}
func fail(e error) { fmt.Fprintln(os.Stderr, "host-agent:", e); os.Exit(1) }
