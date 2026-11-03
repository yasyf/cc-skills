package main

import "github.com/yasyf/cc-skills/plugins/long-running/lr/internal/facts"

func init() {
	register(facts.Command)
}
