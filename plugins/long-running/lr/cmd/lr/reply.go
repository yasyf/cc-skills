package main

import "github.com/yasyf/cc-skills/plugins/long-running/lr/internal/reply"

func init() {
	register(reply.Command)
}
