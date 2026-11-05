package livetasks

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"path/filepath"
	"regexp"
	"runtime"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

var armedWait = regexp.MustCompile(`(?:^|\s)LR_WAIT_ARMED=1(?:\s|$)`)

// Rest is what will wake an Orca terminal's agent once its turn has ended, read
// on the host the terminal runs on, with what was read to say so.
type Rest struct {
	Tasks []string `json:"tasks"`
	Read  string   `json:"read"`
}

// Terminal reads the armed `lr wait` processes bearing handle and, for a Claude
// agent, the background tasks its transcript under claude shows live.
func Terminal(ctx context.Context, runner shell.Runner, handle, claude string, now time.Time) (Rest, error) {
	listed, err := runner.Run(ctx, shell.Command{Argv: processList()})
	if err != nil {
		return Rest{}, fmt.Errorf("list processes: %w", err)
	}
	bearer := regexp.MustCompile(`(?:^|\s)ORCA_TERMINAL_HANDLE=` + regexp.QuoteMeta(handle) + `(?:\s|$)`)
	var waits []string
	agent, kind := "", ""
	for line := range strings.Lines(listed.Stdout) {
		pid, command, found := strings.Cut(strings.TrimSpace(line), " ")
		if !found || !bearer.MatchString(command) {
			continue
		}
		name := filepath.Base(strings.Fields(command)[0])
		switch {
		case armedWait.MatchString(command):
			waits = append(waits, pid)
		case name == "claude" || name == "codex":
			agent, kind = pid, name
		}
	}
	rest := Rest{Read: fmt.Sprintf("%d armed lr wait processes bear terminal %s", len(waits), handle)}
	if len(waits) > 0 {
		rest.Tasks = append(rest.Tasks, "- `lr wait --wake` armed as pid "+strings.Join(waits, ", "))
	}
	switch {
	case kind == "":
		rest.Read += ", and no claude or codex process does"
		return rest, nil
	case kind == "codex":
		rest.Read += fmt.Sprintf("; codex pid %s keeps no transcript lr reads, so only an armed lr wait counts", agent)
		return rest, nil
	}
	session, err := sessionOf(ctx, runner, agent)
	if err != nil {
		return Rest{}, err
	}
	if session == "" {
		rest.Read += fmt.Sprintf("; cci who names no session for claude pid %s", agent)
		return rest, nil
	}
	transcripts, err := filepath.Glob(filepath.Join(claude, "projects", "*", session+".jsonl"))
	if err != nil {
		return Rest{}, err
	}
	if len(transcripts) == 0 {
		rest.Read += fmt.Sprintf("; claude pid %s, session %s, has no transcript under %s", agent, session, filepath.Join(claude, "projects"))
		return rest, nil
	}
	slices.Sort(transcripts)
	tasks, err := Read(transcripts[0], now)
	if err != nil {
		return Rest{}, err
	}
	for _, task := range tasks {
		rest.Tasks = append(rest.Tasks, task.Line())
	}
	rest.Read += fmt.Sprintf("; claude pid %s's transcript %s shows %d live Monitor, background Bash or background Agent tasks", agent, transcripts[0], len(tasks))
	return rest, nil
}

func processList() []string {
	if runtime.GOOS == "darwin" {
		return []string{"ps", "-axE", "-ww", "-o", "pid=,command="}
	}
	return []string{"ps", "axeww", "-o", "pid=,command="}
}

func sessionOf(ctx context.Context, runner shell.Runner, pid string) (string, error) {
	listed, err := runner.Run(ctx, shell.Command{Argv: []string{"cci", "who", "--origin", "-", "--json", "--fields", "session,pid"}})
	if err != nil {
		return "", fmt.Errorf("cci who: %w", err)
	}
	scanner := bufio.NewScanner(strings.NewReader(listed.Stdout))
	for scanner.Scan() {
		var peer struct {
			Session string `json:"session"`
			PID     int    `json:"pid"`
		}
		if err := json.Unmarshal(scanner.Bytes(), &peer); err != nil {
			return "", fmt.Errorf("cci who: %w", err)
		}
		if strconv.Itoa(peer.PID) == pid {
			return peer.Session, nil
		}
	}
	return "", scanner.Err()
}
