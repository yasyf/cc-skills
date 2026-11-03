package main

import "github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"

func init() {
	register(bus.Command)
}
