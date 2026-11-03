package wait

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
)

var unsafeName = regexp.MustCompile(`[^a-zA-Z0-9_-]`)

type roster struct {
	Members []struct {
		Name     string  `json:"name"`
		JoinedAt float64 `json:"joinedAt"`
	} `json:"members"`
}

// Mailbox is the team mailbox of the newest teammate named lane on any roster
// under claude/teams, or empty when no roster lists it.
func Mailbox(claude, lane string) (string, error) {
	if claude == "" {
		claude = os.Getenv("CLAUDE_CONFIG_DIR")
	}
	if claude == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", err
		}
		claude = filepath.Join(home, ".claude")
	}
	configs, err := filepath.Glob(filepath.Join(claude, "teams", "*", "config.json"))
	if err != nil {
		return "", err
	}
	var team string
	newest := -1.0
	for _, config := range configs {
		content, err := os.ReadFile(config)
		if err != nil {
			continue
		}
		var r roster
		if json.Unmarshal(content, &r) != nil {
			continue
		}
		for _, member := range r.Members {
			if member.Name == lane && member.JoinedAt > newest {
				newest, team = member.JoinedAt, filepath.Dir(config)
			}
		}
	}
	if team == "" {
		return "", nil
	}
	return filepath.Join(team, "inboxes", unsafeName.ReplaceAllString(lane, "-")+".json"), nil
}
