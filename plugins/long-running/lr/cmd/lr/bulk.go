package main

import "github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bulk"

func init() {
	register(bulk.Command)
}
