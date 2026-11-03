package main

import "github.com/yasyf/cc-skills/plugins/long-running/lr/internal/incident"

func init() {
	register(incident.Command)
}
