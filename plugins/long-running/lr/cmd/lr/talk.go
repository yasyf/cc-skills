package main

import "github.com/yasyf/cc-skills/plugins/long-running/lr/internal/talk"

func init() {
	register(talk.Command)
}
