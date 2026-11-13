package main

import (
	"time"

	"golang.org/x/sys/unix"
)

func procStart(pid int) string {
	kp, err := unix.SysctlKinfoProc("kern.proc.pid", pid)
	if err != nil {
		return ""
	}
	return time.Unix(kp.Proc.P_starttime.Sec, 0).UTC().Format(lstartLayout)
}
