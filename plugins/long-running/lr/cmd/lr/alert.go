package main

import "github.com/yasyf/cc-skills/plugins/long-running/lr/internal/alert"

func init() {
	register(alert.Command)
}
