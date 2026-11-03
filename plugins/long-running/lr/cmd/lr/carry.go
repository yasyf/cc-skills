package main

import "github.com/yasyf/cc-skills/plugins/long-running/lr/internal/carry"

func init() {
	register(carry.Command)
}
