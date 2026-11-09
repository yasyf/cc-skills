package cardtext

import (
	"regexp"
	"strings"
)

var (
	titleScope = regexp.MustCompile(`^[\w./-]+(?:\([\w./-]+\))?!?:\s+`)
	fixMarks   = []string{"🐛", ":bug:", "🚑", ":ambulance:", "💚", ":green_heart:", "🩹", ":adhesive_bandage:", "🔒", ":lock:", "🚨", ":rotating_light:", "🥅", ":goal_net:", "⏪", ":rewind:", "✏", ":pencil2:", "🔐", ":closed_lock_with_key:"}
	cleanMarks = []string{"🔥", ":fire:", "🗑", ":wastebasket:", "♻", ":recycle:", "🧹", ":broom:", "⚰", ":coffin:", "🚚", ":truck:", "➖", ":heavy_minus_sign:", "🔇", ":mute:", "🎨", ":art:"}
)

func ChangeKind(title string) string {
	rest := titleScope.ReplaceAllString(strings.TrimSpace(title), "")
	for _, mark := range fixMarks {
		if strings.HasPrefix(rest, mark) {
			return "Fix"
		}
	}
	for _, mark := range cleanMarks {
		if strings.HasPrefix(rest, mark) {
			return "Cleanup"
		}
	}
	return "New"
}
