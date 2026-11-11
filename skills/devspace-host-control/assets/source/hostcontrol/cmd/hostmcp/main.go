package main

import (
	"flag"
	"fmt"
	"github.com/loft-sh/devspace/hostcontrol/internal/host"
	"os"
)

func main() {
	config := flag.String("config", "", "client JSON configuration")
	flag.Parse()
	var c host.ClientConfig
	e := host.ReadJSON(*config, &c)
	if e == nil {
		e = c.Validate()
	}
	var call host.Caller
	if e == nil {
		call, e = host.NewCaller(c)
	}
	if e == nil {
		e = host.RunMCP(os.Stdin, os.Stdout, call)
	}
	if e != nil {
		fmt.Fprintln(os.Stderr, "host-mcp:", e)
		os.Exit(1)
	}
}
