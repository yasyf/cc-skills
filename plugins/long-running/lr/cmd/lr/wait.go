package main

import "github.com/yasyf/cc-skills/plugins/long-running/lr/internal/wait"

func init() {
	register(wait.Command)
}
