package main

import "github.com/yasyf/cc-skills/plugins/long-running/lr/internal/update"

func init() {
	register(update.Command)
}
