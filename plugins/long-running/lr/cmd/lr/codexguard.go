package main

import "github.com/yasyf/cc-skills/plugins/long-running/lr/internal/codexguard"

func init() {
	register(codexguard.Command)
}
