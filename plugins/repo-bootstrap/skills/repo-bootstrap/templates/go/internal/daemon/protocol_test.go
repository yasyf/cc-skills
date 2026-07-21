package daemon

import (
	"testing"

	"github.com/yasyf/daemonkit/wire"
	"github.com/yasyf/daemonkit/wire/lifeproto"
)

func TestDaemonkitProtocolSnapshot(t *testing.T) {
	if wire.ProtocolVersion != 1 {
		t.Fatalf("wire.ProtocolVersion = %d, want 1", wire.ProtocolVersion)
	}
	if lifeproto.Version != 1 {
		t.Fatalf("lifeproto.Version = %d, want 1", lifeproto.Version)
	}
	cases := []struct {
		name    string
		message any
		want    string
	}{
		{"health request", lifeproto.NewHealthRequest(), `{"v":1,"op":"health"}`},
		{
			"health response",
			lifeproto.NewHealthResponse("1.0.0", int(wire.ProtocolVersion), 4242, "healthy", false, false),
			`{"v":1,"op":"health","build":"1.0.0","protocol":1,"pid":4242,"state":"healthy","draining":false,"busy":false}`,
		},
		{"shutdown request", lifeproto.NewShutdownRequest(), `{"v":1,"op":"shutdown"}`},
		{"shutdown response", lifeproto.NewShutdownResponse(true), `{"v":1,"op":"shutdown","ok":true}`},
		{"handoff request", lifeproto.NewHandoffRequest(), `{"v":1,"op":"handoff"}`},
		{"handoff response", lifeproto.NewHandoffResponse(true), `{"v":1,"op":"handoff","ok":true}`},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			got, err := lifeproto.Encode(test.message)
			if err != nil {
				t.Fatalf("Encode: %v", err)
			}
			if string(got) != test.want {
				t.Fatalf("payload = %s, want %s", got, test.want)
			}
		})
	}
}
