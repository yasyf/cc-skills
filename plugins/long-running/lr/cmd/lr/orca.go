package main

import "github.com/yasyf/cc-skills/plugins/long-running/lr/internal/orca"

func init() {
	register(orca.GCCommand)
	register(orca.LaunchCommand)
	register(orca.RelayRootCommand)
	register(orca.SpritesCommand)
	register(orca.SparseCommand)
	register(orca.RejoinCommand)
}
