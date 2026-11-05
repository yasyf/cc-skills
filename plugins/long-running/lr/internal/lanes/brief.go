package lanes

import (
	"errors"
	"io/fs"
	"os"
	"regexp"
	"strings"
	"sync"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/owner"
)

var (
	macBrief    = regexp.MustCompile(`(?m)^ccx:(?:.*[ \t])?place=local(?:\s|$)`)
	sliceStep   = regexp.MustCompile(`(?m)^Register slice: lane=\S+ step=(\S+)`)
	standing    = regexp.MustCompile(`(?m)^Standing: *(\S.*?)[ \t]*$`)
	noDuty      = regexp.MustCompile(`(?i)^none\b`)
	deliverable = regexp.MustCompile(`(?m)^(?:#+ *)?(?:\*\*)?Deliverables?\b[^\n:]*:?\**[ \t]*([^\n]*)(?:\n\s*([^\n]*))?`)
)

type Brief struct {
	Step        string
	Standing    string
	Deliverable string
	Orders      []string
}

type cachedBrief struct {
	changed time.Time
	brief   Brief
}

var (
	briefsMu sync.Mutex
	briefs   = map[string]cachedBrief{}
)

func parseBrief(text string) Brief {
	var said Brief
	if found := sliceStep.FindStringSubmatch(text); found != nil {
		said.Step = found[1]
	}
	if found := standing.FindStringSubmatch(text); found != nil && !noDuty.MatchString(found[1]) {
		said.Standing = found[1]
	}
	if found := deliverable.FindStringSubmatch(text); found != nil {
		said.Deliverable = strings.TrimSpace(or(found[1], found[2]))
	}
	said.Orders = owner.Orders(text)
	return said
}

func BriefOf(path string) (Brief, error) {
	info, err := os.Stat(path)
	if errors.Is(err, fs.ErrNotExist) {
		return Brief{}, nil
	}
	if err != nil {
		return Brief{}, err
	}
	briefsMu.Lock()
	defer briefsMu.Unlock()
	if cached, found := briefs[path]; found && cached.changed.Equal(info.ModTime()) {
		return cached.brief, nil
	}
	text, err := os.ReadFile(path)
	if errors.Is(err, fs.ErrNotExist) {
		return Brief{}, nil
	}
	if err != nil {
		return Brief{}, err
	}
	said := parseBrief(string(text))
	briefs[path] = cachedBrief{info.ModTime(), said}
	return said, nil
}

func briefExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.Mode().IsRegular()
}
