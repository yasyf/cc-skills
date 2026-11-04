package main

import "github.com/yasyf/cc-skills/plugins/long-running/lr/internal/board"

func init() {
	register(board.WithdrawCommand)
}
