package reply

import (
	"context"
	"strings"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
)

func TestAReplyWithAFieldTheToolDoesNotKnowPostsNothingAndNamesPicks(t *testing.T) {
	args := []byte(`{"title":"Which shape?","md":"The body.","options":["Keep it :: nothing moves","Drop it :: it goes"],"recommended":"Keep it"}`)
	text, failed := Tool(&app.App{}).Handler(context.Background(), args, func(string) {})
	if !failed || !strings.Contains(text, `unknown field "options"`) || !strings.Contains(text, "picks") {
		t.Fatalf("reply = %q, failed %v; want it refused naming options and picks", text, failed)
	}
}
