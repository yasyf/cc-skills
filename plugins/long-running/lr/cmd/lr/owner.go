package main

import "github.com/yasyf/cc-skills/plugins/long-running/lr/internal/owner"

func init() {
	register(owner.Command)
}
