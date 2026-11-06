package lanes

import (
	"context"
	"fmt"
	"log/slog"
	"regexp"
	"slices"
	"strings"
	"time"
	"unicode"

	"github.com/yasyf/cc-inbox/client"
)

const (
	muteEvery     = time.Minute
	muteBack      = time.Hour
	mutePosted    = "MUTE-PUSHBACK"
	muteKind      = "incident"
	muteMechanism = "mechanism"
	muteOpen      = "open"
	landedKind    = "landed"
	beforeMuted   = 40
	muteTeamWord  = `smoke|teams?|monitors?|alerts?|pages?|runs?|failures?|checks?`
)

var (
	muteKinds = []string{muteKind, muteMechanism, "report", "answer", "stopped", "not-ours"}

	muteProposed = regexp.MustCompile(`(?i)(?:^|[^\w/-])mut(?:e|ed|es|ing)[^\w/.;\n-][^.;\n]{0,39}?\b(?:` + muteTeamWord + `)\b|\bfalse alarm\b|\b(?:skips?|excludes?|scopes? out)\b[^.\n]{0,60}\b(?:smoke|release[- ]check|teams?)\b`)
	muteWatched  = regexp.MustCompile(`(?i)\b(?:mut\w*|monitors?|alerts?|pages?|false alarm)\b`)
	muteNamed    = regexp.MustCompile(`(?i)(?:['’]s|\bthe|\bits|\btheir)\s+$`)
	muteUndone   = regexp.MustCompile(`(?i)\b(?:delet|remov|lift|expir|unmut|drop|clear)\w*`)
	muteWindow   = regexp.MustCompile(`^MUTE [\w.-]+: monitors \d+(?:,? \d+)* until fix-live: \S`)
	releaseGates = regexp.MustCompile(`(?i)\b(?:smoke[- ]?(?:teams?|tests?|runs?|checks?)|release[- ](?:checks?|smokes?|gates?)|dev[- ]red|dev is red)\b`)
)

type MutePushback struct {
	Env    Env
	Log    *slog.Logger
	read   time.Time
	raised map[int64]bool
}

func (m *MutePushback) Check(ctx context.Context, now time.Time) error {
	if !m.read.IsZero() && now.Sub(m.read) < muteEvery {
		return nil
	}
	m.read = now
	rows, err := m.Env.Bus.Tail(ctx, client.Query{Drive: m.Env.Config.Drive, Since: now.Add(-muteBack)})
	if err != nil {
		m.Log.Warn("mute pushback did not read the drive", "err", err)
		return nil
	}
	incidents, err := m.Env.Bus.Open(ctx, m.Env.Config.Drive, muteKind)
	if err != nil {
		m.Log.Warn("mute pushback did not read the open incidents", "err", err)
		return nil
	}
	bySeq := make(map[int64]client.Record, len(incidents)+len(rows))
	for _, row := range slices.Concat(incidents, rows) {
		bySeq[row.Seq] = row
	}
	fixed := fixedByOrderedLane(rows)
	for _, row := range rows {
		said := proposedMute(row)
		if said == "" || m.raised[row.Seq] || row.Lane == Root || (fixed[row.Lane] != 0 && fixed[row.Lane] < row.Seq) || !watched(row, bySeq) {
			continue
		}
		text := fmt.Sprintf("%s %s: %s proposes \"%s\" while its mechanism is unfixed; a mute or a monitor that skips the failing teams is never a close, and smoke teams are the canary (owner, Nov 1) | coordinator: push back where it was proposed, keep the incident open, and name its fix owner", mutePosted, row.Lane, row.Name(), said)
		if _, err := m.Env.Bus.Post(ctx, client.Draft{Drive: m.Env.Config.Drive, Lane: Self, Kind: muteKind, To: []string{Root}, ID: fmt.Sprintf("mute-pushback:%d", row.Seq), Text: text}); err != nil {
			return fmt.Errorf("%w: cci post: %w", ErrUnread, err)
		}
		if m.raised == nil {
			m.raised = map[int64]bool{}
		}
		m.raised[row.Seq] = true
	}
	return nil
}

func fixedByOrderedLane(rows []client.Record) map[string]int64 {
	ordered := map[string]bool{}
	orders := map[int64]bool{}
	for _, row := range rows {
		if row.Lane == Root && row.Kind == muteKind {
			orders[row.Seq] = true
			for _, lane := range row.To {
				ordered[lane] = true
			}
		}
	}
	fixed := map[string]int64{}
	for _, row := range rows {
		if orders[row.Re] {
			ordered[row.Lane] = true
		}
	}
	for _, row := range rows {
		if _, seen := fixed[row.Lane]; !seen && row.Kind == landedKind && (ordered[row.Lane] || ordered[client.Stem(row.Lane)]) {
			fixed[row.Lane] = row.Seq
		}
	}
	return fixed
}

func watched(row client.Record, bySeq map[int64]client.Record) bool {
	for seen := map[int64]bool{}; !seen[row.Seq]; {
		seen[row.Seq] = true
		attrs := row.Fields.Attrs
		if row.Kind == muteMechanism || (row.Kind == muteKind && row.Status == muteOpen) || attrs["monitor"] != "" || attrs["incident"] != "" || alertURL.MatchString(row.Refs.URL) {
			return true
		}
		parent, known := bySeq[row.Re]
		if !known {
			return false
		}
		row = parent
	}
	return false
}

func proposedMute(row client.Record) string {
	text := row.Text
	if !slices.Contains(muteKinds, string(row.Kind)) || strings.HasPrefix(text, mutePosted) || (muteWindow.MatchString(text) && !releaseGates.MatchString(text)) {
		return ""
	}
	for _, found := range muteProposed.FindAllStringIndex(text, -1) {
		said := strings.TrimLeftFunc(text[found[0]:found[1]], func(r rune) bool { return !unicode.IsLetter(r) })
		start := found[1] - len(said)
		if !muteNamed.MatchString(text[:start]) && !muteUndone.MatchString(text[max(0, start-beforeMuted):found[1]]) && muteWatched.MatchString(sentenceOf(text, start, found[1])) {
			return said
		}
	}
	return ""
}

func sentenceOf(text string, start, end int) string {
	from := max(strings.LastIndex(text[:start], ". "), strings.LastIndex(text[:start], "\n")) + 1
	to := len(text)
	for _, stop := range []string{". ", "\n"} {
		if at := strings.Index(text[end:], stop); at != -1 {
			to = min(to, end+at)
		}
	}
	return text[from:to]
}
