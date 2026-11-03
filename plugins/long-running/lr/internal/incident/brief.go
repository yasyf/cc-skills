package incident

import (
	"embed"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"
	_ "time/tzdata"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	RoleFix      = "fix"
	RoleEvidence = "evidence"
	RoleComms    = "comms"
	RoleBackup   = "backup"
	briefSuffix  = ".full.md"
	dueAfter     = 20 * time.Minute
)

//go:embed briefs/fix.txt briefs/evidence.txt
var briefTemplates embed.FS

var (
	placeholder = regexp.MustCompile(`<[a-z][a-z -]*>`)
	briefFields = []string{"records", "adopted", "comms_lane", "bus", "reported", "alert", "code_path", "runbook", "rulings", "entry_point", "common", "routes"}
	sections    = map[string]string{RoleFix: "fix", RoleEvidence: "evidence", RoleBackup: "fix"}
	pacificZone = mustZone("America/Los_Angeles")
)

func mustZone(name string) *time.Location {
	zone, err := time.LoadLocation(name)
	if err != nil {
		panic(err)
	}
	return zone
}

func pacificAt(at time.Time) string {
	return strings.ToLower(at.In(pacificZone).Format("3:04PM"))
}

func pacific(stamp string) string {
	at, err := store.Parse(stamp)
	if err != nil {
		panic(fmt.Sprintf("stored stamp %q: %v", stamp, err))
	}
	return pacificAt(at)
}

func sender(id string) string {
	return "incident-" + id
}

func topic(id string) string {
	return "incident:" + id
}

func laneName(id, role string) string {
	return "incident-" + id + "-" + role
}

func incidentLane(id string, facts Facts, role string) string {
	if lane := facts.Adopted[role]; lane != "" {
		return lane
	}
	return laneName(id, role)
}

func renderBrief(role string, values map[string]string) (string, error) {
	body, err := briefTemplates.ReadFile("briefs/" + sections[role] + ".txt")
	if err != nil {
		return "", err
	}
	return placeholder.ReplaceAllStringFunc(string(body), func(match string) string {
		if value, ok := values[match[1:len(match)-1]]; ok {
			return value
		}
		return "n/a"
	}), nil
}

func writeBrief(id string, facts Facts, role, dir string) (string, error) {
	reported, err := store.Parse(facts.Reported)
	if err != nil {
		return "", fmt.Errorf("reported time: %w", err)
	}
	records := RecordIDs{Investigation: "n/a", Log: "n/a"}
	if facts.Records != nil {
		records = *facts.Records
	}
	rulings := "none recorded for this subsystem"
	if len(facts.Rulings) > 0 {
		rulings = strings.Join(facts.Rulings, "\n  ")
	}
	entryPoint := facts.EntryPoint
	if entryPoint == "" {
		entryPoint = "none named"
	}
	runbook := facts.Runbook
	if runbook == "" {
		runbook = "n/a"
	}
	text, err := renderBrief(role, map[string]string{
		"investigation id":       records.Investigation,
		"incident log id":        records.Log,
		"fix lane name":          incidentLane(id, facts, RoleFix),
		"evidence lane name":     incidentLane(id, facts, RoleEvidence),
		"comms lane name":        facts.CommsLane,
		"coordinator agent name": sender(id),
		"bus id":                 facts.Bus,
		"incident topic":         topic(id),
		"incident id":            id,
		"report time":            pacificAt(reported),
		"due time":               pacificAt(reported.Add(dueAfter)),
		"alert link":             facts.Alert,
		"named code path":        facts.CodePath,
		"runbook":                runbook,
		"submit skill":           "submit-pr",
		"break-glass skill":      "break-glass",
		"design rulings":         rulings,
		"entry point":            entryPoint,
	})
	if err != nil {
		return "", err
	}
	if facts.Common != "" {
		common, err := os.ReadFile(facts.Common)
		if err != nil {
			return "", err
		}
		text = string(common) + "\n" + text
	}
	if err := os.MkdirAll(dir, 0o750); err != nil {
		return "", err
	}
	path := filepath.Join(dir, laneName(id, role)+briefSuffix)
	return path, os.WriteFile(path, []byte(text), 0o600)
}
