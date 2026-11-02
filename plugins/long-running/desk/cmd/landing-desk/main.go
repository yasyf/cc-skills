// Command landing-desk is the Go port of desk-runner.py's landing desk: `landing-desk loop --config <runner.json> [--once]` runs run_landing.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"

	"github.com/yasyf/cc-skills/plugins/long-running/desk/runner"
	"github.com/yasyf/cc-skills/plugins/long-running/desk/shell"
)

func main() {
	if len(os.Args) < 2 || os.Args[1] != "loop" {
		fmt.Fprintln(os.Stderr, "usage: landing-desk loop --config <runner.json> [--once]")
		os.Exit(2)
	}
	verb := flag.NewFlagSet("loop", flag.ExitOnError)
	config := verb.String("config", "", "the drive's runner.json")
	once := verb.Bool("once", false, "run one pass and return")
	verb.Parse(os.Args[2:])
	if *config == "" {
		fmt.Fprintln(os.Stderr, "landing-desk loop: --config is required")
		os.Exit(2)
	}
	sh := open()
	defer func() {
		if failure := recover(); failure != nil {
			if _, ok := failure.(*shell.Raised); ok {
				os.Exit(shell.ExitRaised)
			}
			panic(failure)
		}
	}()
	os.Exit(runner.RunLanding(runner.New(sh, runner.LoadConfig(*config, sh), version(sh)), *once))
}

func open() shell.Shell {
	if socket := os.Getenv(shell.BridgeEnv); socket != "" {
		return shell.NewBridge(socket)
	}
	return shell.NewReal()
}

func version(sh shell.Shell) string {
	manifest, err := os.ReadFile(filepath.Join(sh.Getenv(runner.PluginRootEnv), ".claude-plugin", "plugin.json"))
	if err != nil {
		panic(err)
	}
	var plugin struct {
		Version string `json:"version"`
	}
	if err := json.Unmarshal(manifest, &plugin); err != nil {
		panic(err)
	}
	return plugin.Version
}
