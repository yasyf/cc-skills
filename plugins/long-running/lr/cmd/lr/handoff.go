package main

import "github.com/yasyf/cc-skills/plugins/long-running/lr/internal/handoff"

func init() {
	register(handoff.Command)
}
