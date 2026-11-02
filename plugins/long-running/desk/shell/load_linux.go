package shell

import (
	"os"
	"strconv"
	"strings"
)

func loadAverage() float64 {
	raw, err := os.ReadFile("/proc/loadavg")
	if err != nil {
		panic(err)
	}
	load, err := strconv.ParseFloat(strings.Fields(string(raw))[0], 64)
	if err != nil {
		panic(err)
	}
	return load
}
