package awake

import (
	"encoding/json"
	"fmt"
	"regexp"
	"strings"
)

const (
	lastKernelKept = 3
	wokenWithin    = 180
)

var kernelTrouble = regexp.MustCompile(`^<[0-4]>`)

func LastWords(samples string) string {
	boots := [][]Sample{}
	for line := range strings.Lines(samples) {
		var sample Sample
		if err := json.Unmarshal([]byte(line), &sample); err != nil {
			continue
		}
		if len(boots) == 0 || sample.UptimeSeconds < boots[len(boots)-1][len(boots[len(boots)-1])-1].UptimeSeconds {
			boots = append(boots, nil)
		}
		boots[len(boots)-1] = append(boots[len(boots)-1], sample)
	}
	if len(boots) == 0 {
		return "it left no sample in " + SamplePath
	}
	stopped := boots[len(boots)-1]
	if len(boots) > 1 && stopped[len(stopped)-1].UptimeSeconds < wokenWithin {
		stopped = boots[len(boots)-2]
	}
	var trouble []string
	for _, sample := range stopped {
		for _, line := range sample.Kernel {
			if kernelTrouble.MatchString(line) {
				trouble = append(trouble, line)
			}
		}
	}
	said := "no kernel error on " + homeDisk
	if len(trouble) > 0 {
		said = strings.Join(trouble[max(0, len(trouble)-lastKernelKept):], "; ")
	}
	last := stopped[len(stopped)-1]
	return fmt.Sprintf("its last sample before the stop, %s at uptime %d s, read %d MB free, %d OOM kills, load %s, %d waiting on disk, %d agents; %s",
		last.Time, int(last.UptimeSeconds), last.MemAvailableMB, last.OOMKills, strings.SplitN(last.Load, " ", 2)[0], last.DiskWaiting, len(last.Agents), said)
}
