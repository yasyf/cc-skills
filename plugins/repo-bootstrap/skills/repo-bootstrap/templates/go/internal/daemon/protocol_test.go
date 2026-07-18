package daemon

import (
	"encoding/json"
	"testing"

	"github.com/yasyf/daemonkit/wire/lifeproto"
)

// TestLifeprotoGoldenBytes freezes the exact on-wire bytes of every lifecycle op
// {{PROJECT_NAME}}d speaks. daemonkit's lifeproto envelope is frozen after v1; any
// drift here breaks compatibility with an already-deployed daemon or Swift peer.
// TODO(bootstrap): add your own request/response ops as the protocol grows.
func TestLifeprotoGoldenBytes(t *testing.T) {
	if lifeproto.Version != 1 {
		t.Fatalf("lifeproto.Version = %d, want 1", lifeproto.Version)
	}
	cases := []struct {
		name  string
		value any
		want  string
	}{
		{"health request", lifeproto.NewHealthRequest(), `{"v":1,"op":"health"}`},
		{
			"health response",
			lifeproto.NewHealthResponse("1.0.0", 4242, "healthy", false, false, nil),
			`{"v":1,"op":"health","version":"1.0.0","pid":4242,"state":"healthy","draining":false,"busy":false,"features":[]}`,
		},
		{"shutdown request", lifeproto.NewShutdownRequest(), `{"v":1,"op":"shutdown"}`},
		{"hello response", lifeproto.NewHelloResponse(nil), `{"v":1,"op":"hello","features":[]}`},
		{"handoff request", lifeproto.NewHandoffRequest(), `{"v":1,"op":"handoff"}`},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, err := json.Marshal(tc.value)
			if err != nil {
				t.Fatalf("marshal: %v", err)
			}
			if string(got) != tc.want {
				t.Errorf("golden mismatch\n got: %s\nwant: %s", got, tc.want)
			}
		})
	}
}
