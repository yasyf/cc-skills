package main

import "github.com/yasyf/cc-skills/plugins/long-running/lr/internal/supervisor"

func init() {
	register(supervisor.Command)
}
