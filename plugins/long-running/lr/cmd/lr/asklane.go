package main

import "github.com/yasyf/cc-skills/plugins/long-running/lr/internal/asklane"

func init() {
	register(asklane.Command)
}
