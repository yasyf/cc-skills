package orca

import (
	"bytes"
	"cmp"
	"context"
	"fmt"
	"io"
	"os"
	"regexp"
	"strings"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/incident"
)

const (
	stageKeyEnv   = "LAUNCH_STAGE_KEY"
	fallbackEnv   = "LAUNCH_FALLS_BACK"
	stageDriveEnv = "LAUNCH_STAGE_DRIVE"
	withdrawnEnv  = "LAUNCH_WITHDRAWN"
	withdrawnStop = "withdrawn by "
)

var (
	prepareMessage = regexp.MustCompile(`\bmsg=(?:"([^"]+)"|(\S+))`)
	prepareBox     = regexp.MustCompile(`\b(?:workspace|machine|resource)=(\S+)`)
	prepareStages  = map[string]string{
		"claimed a warm workspace": "claimed warm Sprite {}",
		"a claimed warm workspace failed before its worker started, so the lane tries the next": "warm Sprite {} failed; trying the next",
		"creating": "no warm Sprite free; creating {}",
		"the tools are ready at the current stamp":                           "tools ready on {}",
		"the tailnet no longer holds this workspace's node; enrolling again": "{} rejoining the tailnet",
		"the Orca runtime is ready":                                          "Orca runtime ready on {}",
		"started the worker":                                                 "agent started",
	}
)

type launchStages struct {
	bus              Bus
	drive, key, lane string
	stderr           io.Writer
}

func stagesOf(env func(string) string, bus Bus, lane string, stderr io.Writer) launchStages {
	return launchStages{bus: bus, drive: env(stageDriveEnv), key: env(stageKeyEnv), lane: lane, stderr: stderr}
}

func (s launchStages) say(ctx context.Context, stage string) {
	if s.key == "" {
		return
	}
	draft := client.Draft{Drive: s.drive, Lane: deskRunner, Kind: kindState, Topic: s.key, Text: incident.StageText(s.key, s.lane, stage)}
	if _, err := s.bus.Post(ctx, draft); err != nil {
		fmt.Fprintf(s.stderr, "stage %q left unposted: %v\n", stage, err)
	}
}

func withdrawnBy(env func(string) string) string {
	raw, _ := os.ReadFile(env(withdrawnEnv))
	return strings.TrimSpace(string(raw))
}

func prepareStage(line string) string {
	message := prepareMessage.FindStringSubmatch(line)
	if message == nil {
		return ""
	}
	stage := prepareStages[cmp.Or(message[1], message[2])]
	if box := prepareBox.FindStringSubmatch(line); box != nil {
		return strings.ReplaceAll(stage, "{}", box[1])
	}
	return stage
}

type lineWriter struct {
	line func(string)
	rest []byte
}

func (w *lineWriter) Write(chunk []byte) (int, error) {
	w.rest = append(w.rest, chunk...)
	for {
		end := bytes.IndexByte(w.rest, '\n')
		if end < 0 {
			return len(chunk), nil
		}
		w.line(string(w.rest[:end]))
		w.rest = w.rest[end+1:]
	}
}
