package shell

import (
	"encoding/binary"

	"golang.org/x/sys/unix"
)

func loadAverage() float64 {
	raw, err := unix.SysctlRaw("vm.loadavg")
	if err != nil {
		panic(err)
	}
	return float64(binary.LittleEndian.Uint32(raw[0:4])) / float64(binary.LittleEndian.Uint64(raw[16:24]))
}
