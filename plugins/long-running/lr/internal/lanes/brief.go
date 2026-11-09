package lanes

import (
	"errors"
	"io/fs"
	"os"
	"regexp"
	"slices"
	"strings"
	"sync"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/owner"
)

var (
	macBrief    = regexp.MustCompile(`(?m)^ccx:(?:.*[ \t])?place=local(?:\s|$)`)
	macNeed     = regexp.MustCompile(`(?m)^ccx:(?:.*[ \t])?why=(\S+).*$`)
	briefRole   = regexp.MustCompile(`(?m)^ccx:(?:.*[ \t])?role=([\w-]+)`)
	briefLane   = regexp.MustCompile(`(?m)^ccx:(?:.*[ \t])?lane=([\w.-]*\w)`)
	sliceStep   = regexp.MustCompile(`(?m)^Register slice: lane=\S+ step=(\S+)`)
	standing    = regexp.MustCompile(`(?m)^Standing: *(\S.*?)[ \t]*$`)
	noDuty      = regexp.MustCompile(`(?i)^none\b`)
	deliverable = regexp.MustCompile(`(?m)^(?:#+ *)?(?:\*\*)?Deliverables?\b[^\n:]*:?\**[ \t]*([^\n]*)(?:\n\s*([^\n]*))?`)

	BriefAssignedOrders = regexp.MustCompile(`(?m)^` + AssignedOrdersLine + `(.*)$`)
	DeskBrief           = regexp.MustCompile(`(?m)^ccx:(.* )?role=(desk|watch)(\s|$)`)

	MacNeeds = []string{"sentry", "datadog", "signed-in", "cc-present", "mac-build", "mac-state", "devbox", "aws-machine-profile"}
)

const (
	AssignedOrdersLine = "Assigned orders:"
	MacNeedsLine       = "why=sentry or why=datadog to read evidence there, why=signed-in for a proof in a signed-in session, why=cc-present to serve the owner's board or another page he opens on this Mac, why=mac-build for a build or install only this Mac runs, why=mac-state to read or change this Mac's own stores, worktrees or processes, why=devbox to claim a devbox or EC2 Mac box with this Mac's AWS identity, or why=aws-machine-profile for an AWS read or tools/ci call, such as scripts/stack-enqueue, that runs on one of this Mac's machine-sourced AWS profiles"
)

type Brief struct {
	Lane        string
	Role        string
	Step        string
	Standing    string
	Deliverable string
	Orders      []string
	OwnerPicked bool
	Desk        bool
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
	if found := briefLane.FindStringSubmatch(text); found != nil {
		said.Lane = found[1]
	}
	if found := briefRole.FindStringSubmatch(text); found != nil {
		said.Role = found[1]
	}
	if found := sliceStep.FindStringSubmatch(text); found != nil {
		said.Step = found[1]
	}
	if found := standing.FindStringSubmatch(text); found != nil && !noDuty.MatchString(found[1]) && !slices.Contains(oneShotRoles, said.Role) {
		said.Standing = found[1]
	}
	if found := deliverable.FindStringSubmatch(text); found != nil {
		said.Deliverable = strings.TrimSpace(or(found[1], found[2]))
	}
	if found := BriefAssignedOrders.FindStringSubmatch(text); found != nil {
		said.Orders = owner.Orders(found[1])
	}
	said.OwnerPicked = BriefOwnerPick.MatchString(text)
	said.Desk = DeskBrief.MatchString(text)
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

func WithDuty(body []byte, duty string) []byte {
	if duty == "" || standing.Match(body) {
		return body
	}
	return append([]byte("Standing: "+duty+"\n"), body...)
}

func DeskOnMac(body []byte) bool {
	own := macNeed.FindSubmatch(body)
	return macBrief.Match(body) && own != nil && slices.Contains(MacNeeds, string(own[1]))
}

func briefExists(path string) bool {
	info, err := os.Stat(path)
	return err == nil && info.Mode().IsRegular()
}
