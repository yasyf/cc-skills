package main

import "github.com/yasyf/cc-skills/plugins/long-running/lr/internal/grading"

func init() {
	register(grading.Command)
}
