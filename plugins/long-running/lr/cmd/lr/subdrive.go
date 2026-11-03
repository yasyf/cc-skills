package main

import "github.com/yasyf/cc-skills/plugins/long-running/lr/internal/subdrive"

func init() {
	register(subdrive.Command)
}
