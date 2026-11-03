package main

import "github.com/yasyf/cc-skills/plugins/long-running/lr/internal/asks"

func init() {
	register(asks.Command)
}
