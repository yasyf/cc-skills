package main

import "github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"

func init() {
	register(lanes.Command)
	register(lanes.RouteCommand)
	register(lanes.RequestCommand)
	register(lanes.DoneCheckCommand)
	register(lanes.StopSpritesCommand)
	register(lanes.NscCommand)
	register(lanes.DevboxCommand)
	register(lanes.ApprovedCommand)
	register(lanes.WhereCommand)
}
