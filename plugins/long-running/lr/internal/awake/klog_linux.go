//go:build linux

package awake

import "syscall"

const (
	syslogReadAll = 3
	kernelLogSize = 256 << 10
)

func KernelLog() ([]byte, error) {
	buf := make([]byte, kernelLogSize)
	n, err := syscall.Klogctl(syslogReadAll, buf)
	if err != nil {
		return nil, err
	}
	return buf[:n], nil
}
