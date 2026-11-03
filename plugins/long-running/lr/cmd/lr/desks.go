package main

import "github.com/yasyf/cc-skills/plugins/long-running/lr/internal/desks"

func init() {
	register(desks.Command)
}
