package main

import "github.com/yasyf/cc-skills/plugins/long-running/lr/internal/waste"

func init() {
	register(waste.Command)
}
