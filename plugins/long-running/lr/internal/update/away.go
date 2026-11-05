package update

import (
	"cmp"
	"fmt"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/board"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/plans"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	awayTitle   = "While you were away"
	waitsTitle  = "Waits on you"
	planTitle   = "The plan"
	elseFeature = "Everything else"
	pullURL     = "https://github.com/%s/pull/%d"
	clockLayout = "3:04 PM"

	liveOrReleasing = "live or releasing now"
)

type liveness struct {
	read bool
	owed map[int][]string
}

func livenessOf(records []Record) liveness {
	for _, record := range slices.Backward(bySeq(records)) {
		if record.Topic != lanes.NotLiveTopic {
			continue
		}
		if owed, found := lanes.NotLiveOf(record.Text); found {
			return liveness{true, owed}
		}
	}
	return liveness{}
}

func (l liveness) of(number int, landed, now time.Time) (string, bool) {
	targets, owed := l.owed[number]
	switch age := now.Sub(landed); {
	case owed:
		return "not live yet on " + strings.Join(targets, ", "), false
	case age < lanes.NotLiveOwed+lanes.NotLiveEvery:
		return "landed; the release check reads it at " + pacific(landed.Add(lanes.NotLiveOwed+lanes.NotLiveEvery)), false
	case age >= lanes.NotLiveSpan:
		return "landed over a day ago, past the release check", false
	case !l.read:
		return "no release check recorded in a day", false
	}
	return liveOrReleasing, true
}

type away struct {
	in    Inputs
	v     view
	since time.Time
	owner map[int]string
	live  liveness
}

type feature struct {
	waits, landed, broke []string
}

func Away(in Inputs) string {
	v := newView(in.Lanes, in.Records, in.Waits, in.Now)
	v.rulings = rulingsReplacer(in.Rulings)
	v.history = bySeq(slices.Concat(in.Records, in.All))
	a := away{in: in, v: v, since: in.Before.At, owner: map[int]string{}, live: livenessOf(in.All)}
	for _, lane := range in.Lanes {
		for _, number := range lane.PRs {
			a.owner[number] = lane.Name
		}
	}
	features := make([]feature, len(in.Streams)+1)
	waits := a.waits(features)
	landed, live := a.landed(features)
	broke := a.broke(features)
	unowned := unownedStepLines(in.Plans, in.Lanes)
	sections := make([][]string, 0, len(features)+4)
	sections = append(sections,
		[]string{
			fmt.Sprintf("# %s: %s to %s", awayTitle, stamp(a.since, in.Now), pacific(in.Now)),
			"",
			fmt.Sprintf("Waits on you: %d. Landed: %d, %s: %d. Broke: %d. Plan steps with no owner: %d.", waits, landed, liveOrReleasing, live, broke, len(unowned)),
		},
		unownedStepsSection(unowned),
		a.waitLines(features),
	)
	for at, each := range features {
		sections = append(sections, a.featureLines(at, each))
	}
	sections = append(sections, titled(planTitle, a.planLines()))
	return strings.Join(slices.Concat(sections...), "\n") + "\n"
}

func (a away) streamOf(held func(Workstream) bool) int {
	if at := slices.IndexFunc(a.in.Streams, held); at >= 0 {
		return at
	}
	return len(a.in.Streams)
}

func (a away) laneStream(name string) int {
	lane := a.v.laneNamed(name)
	return a.streamOf(func(each Workstream) bool { return each.holds(lane) })
}

func (a away) prOwner(pr store.ReviewRow) string {
	return cmp.Or(pr.Lane, a.owner[pr.Number])
}

func prLink(pr store.ReviewRow) string {
	link := fmt.Sprintf("[%s](%s)", strings.ReplaceAll(clip(pr.Title), "]", ")"), fmt.Sprintf(pullURL, pr.Repo, pr.Number))
	if pr.ForUsers != "" {
		link += "; for users: " + clip(pr.ForUsers)
	}
	return link
}

func alias(name string) string {
	if name == "" {
		return nobody
	}
	return client.Stem(name)
}

func (a away) waits(features []feature) int {
	rows := map[string]store.ReviewRow{}
	for _, pr := range a.in.PRRows {
		rows[fmt.Sprint(pr.Number)] = pr
	}
	for _, card := range a.in.Cards {
		if card.Kind == "tap" {
			pr := rows[card.PR]
			at := a.laneStream(a.prOwner(pr))
			features[at].waits = append(features[at].waits, fmt.Sprintf("- Approve %s, ready since %s", prLink(pr), card.Since))
			continue
		}
		at := a.streamOf(func(each Workstream) bool { return onCard(card, each) })
		features[at].waits = append(features[at].waits, pickLine(card))
	}
	return len(a.in.Cards)
}

func pickLine(card board.Wait) string {
	buttons := "no buttons recorded"
	if len(card.Options) > 0 {
		buttons = strings.Join(card.Options, " / ")
	}
	where := "on your board"
	if card.URL != "" {
		where = "in Slack: " + card.URL
	}
	return fmt.Sprintf("- %s\n  Buttons: %s. Answer %s.", card.Title, buttons, where)
}

func (a away) landed(features []feature) (landed, live int) {
	type landing struct {
		pr store.ReviewRow
		at time.Time
	}
	var found []landing
	for _, pr := range a.in.PRRows {
		at, err := time.Parse(time.RFC3339, pr.LandedAt)
		if pr.State == prrow.StateLanded && err == nil && at.After(a.since) {
			found = append(found, landing{pr, at})
		}
	}
	slices.SortFunc(found, func(one, other landing) int { return one.at.Compare(other.at) })
	for _, each := range found {
		word, isLive := a.live.of(each.pr.Number, each.at, a.in.Now)
		if isLive {
			live++
		}
		at := a.laneStream(a.prOwner(each.pr))
		features[at].landed = append(features[at].landed, fmt.Sprintf("- %s, landed %s: %s", prLink(each.pr), stamp(each.at, a.in.Now), word))
	}
	return len(found), live
}

func (a away) broke(features []feature) int {
	count := 0
	records := slices.DeleteFunc(slices.Clone(a.in.All), func(record Record) bool { return !incidentRecord(record) })
	for _, incident := range a.v.incidents(records, a.in.Drive) {
		if !incident.Open && !incident.Fired.At.After(a.since) {
			continue
		}
		line := fmt.Sprintf("- Incident %s, fired %s: %s; on it: %s", incident.Name, stamp(incident.Fired.At, a.in.Now), incident.state(), incident.work.who)
		if described := describeIncident(records, incident.Name); described != "" {
			line += ". " + clip(described)
		}
		at := a.laneStream(incident.Name)
		features[at].broke = append(features[at].broke, line)
		count++
	}
	for _, pr := range openPRRows(a.in.PRRows) {
		var wrong string
		switch {
		case pr.CI == prrow.CIRed:
			wrong = prWrong["red"]
		case pr.TapRefused != "" && pr.TapRefused != prrow.None:
			wrong = prWrong["refused"]
		default:
			continue
		}
		at := a.laneStream(a.prOwner(pr))
		features[at].broke = append(features[at].broke, fmt.Sprintf("- %s: %s; owner %s", prLink(pr), wrong, alias(a.prOwner(pr))))
		count++
	}
	return count
}

func (a away) streamName(at int) (string, string) {
	if at == len(a.in.Streams) {
		return elseFeature, "work no workstream holds"
	}
	return a.in.Streams[at].Name, a.in.Streams[at].Delivers
}

func (a away) waitLines(features []feature) []string {
	var lines []string
	if a.in.Unboarded != nil {
		lines = append(lines, fmt.Sprintf("- Your board did not read, so this list may be short: %v", a.in.Unboarded))
	}
	if a.in.TapQueue != "" {
		lines = append(lines, "- Every approval is also on "+a.in.TapQueue)
	}
	for at, each := range features {
		if len(each.waits) == 0 {
			continue
		}
		name, _ := a.streamName(at)
		lines = append(lines, "", "### "+name)
		lines = append(lines, each.waits...)
	}
	if !slices.ContainsFunc(features, func(each feature) bool { return len(each.waits) > 0 }) && a.in.Unboarded == nil {
		lines = append(lines, "- Nothing waits on you.")
	}
	return titled(waitsTitle, lines)
}

func (a away) featureLines(at int, each feature) []string {
	name, delivers := a.streamName(at)
	lines := []string{"", "## " + name, "Delivers " + delivers + "."}
	if at < len(a.in.Streams) {
		stream := a.in.Streams[at]
		on := a.v.workstreamLanes(stream, nil)
		if mine := lanesWhere(a.in.Lanes, func(lane Lane) bool { return stream.holds(lane) && heardLive(lane, a.in.Now) }); len(mine) > 0 {
			on = strings.Join(aliases(namesOf(mine)), ", ")
		}
		lines = append(lines, "On it: "+on)
	}
	if len(each.landed) == 0 && len(each.broke) == 0 {
		return append(lines, fmt.Sprintf("Nothing landed or broke since %s.", stamp(a.since, a.in.Now)))
	}
	if len(each.landed) > 0 {
		lines = append(append(lines, fmt.Sprintf("Landed (%d):", len(each.landed))), each.landed...)
	}
	if len(each.broke) > 0 {
		lines = append(append(lines, fmt.Sprintf("Broke (%d):", len(each.broke))), each.broke...)
	}
	return lines
}

func aliases(names []string) []string {
	out := make([]string, len(names))
	for i, name := range names {
		out[i] = alias(name)
	}
	return unique(out)
}

func (a away) planLines() []string {
	var lines []string
	for _, plan := range a.in.Plans {
		title := plainTitle(a.v.rulings.Replace(plan.Title))
		if plan.Status == planDraft {
			title += ", a draft"
		}
		lines = append(lines, "", "### "+title)
		var done []string
		for _, step := range plan.Steps {
			if step.Number == plans.Unmapped {
				continue
			}
			name := plainTitle(a.v.rulings.Replace(step.Title))
			if stepIsDone(step) {
				done = append(done, name)
				continue
			}
			who := nobody + "; " + lastHolder(a.in.Lanes, step)
			if owners := stepOwners(a.in.Lanes, step); len(owners) > 0 {
				who = a.ownerWords(owners)
			}
			lines = append(lines, fmt.Sprintf("- %s: %d of %d tasks done. Owner: %s", name, step.Count(plans.Done), len(step.Tasks), who))
		}
		if len(done) > 0 {
			lines = append(lines, "- Done: "+strings.Join(done, "; "))
		}
	}
	return lines
}

func (a away) ownerWords(owners []Lane) string {
	words := make([]string, len(owners))
	for i, owner := range owners {
		words[i] = alias(owner.Name)
		if !heardLive(owner, a.in.Now) {
			words[i] += ", quiet since " + stamp(owner.Heard, a.in.Now)
		}
	}
	return strings.Join(unique(words), "; ")
}

func sinceOf(text string, now time.Time) (time.Time, error) {
	if at, err := time.Parse(time.RFC3339, text); err == nil {
		return at, nil
	}
	if back, err := time.ParseDuration(text); err == nil {
		return now.Add(-back), nil
	}
	clock, err := time.ParseInLocation(clockLayout, strings.ToUpper(strings.TrimSpace(text)), pacificZone)
	if err != nil {
		return time.Time{}, fmt.Errorf("--since %q: give an RFC 3339 time, a duration such as 8h, or a Pacific clock time such as 11:30 PM", text)
	}
	local := now.In(pacificZone)
	at := time.Date(local.Year(), local.Month(), local.Day(), clock.Hour(), clock.Minute(), 0, 0, pacificZone)
	if at.After(now) {
		at = at.AddDate(0, 0, -1)
	}
	return at, nil
}
