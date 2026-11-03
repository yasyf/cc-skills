package main

import "github.com/yasyf/cc-skills/plugins/long-running/lr/internal/coordinator"

func init() {
	register(coordinator.Command)
}
