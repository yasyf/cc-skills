package main

import "github.com/yasyf/cc-skills/plugins/long-running/lr/internal/deletions"

func init() {
	register(deletions.Command)
}
