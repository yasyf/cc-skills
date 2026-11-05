package obs

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/askdesk"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	drive       = "brook"
	lane        = "alert-1-fix"
	sentryValue = "sentry-token-never-in-a-record"
	ddAPIValue  = "dd-api-key-never-in-a-record"
	ddAppValue  = "dd-app-key-never-in-a-record"
)

var started = time.Date(2026, 11, 5, 20, 0, 0, 0, time.UTC)

type fakeRunner struct {
	mu    sync.Mutex
	calls []shell.Command
	empty bool
}

func (f *fakeRunner) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls = append(f.calls, c)
	if c.Argv[0] != "aws" {
		return shell.Result{Stdout: "incident inc-1 open\n"}, nil
	}
	if f.empty {
		return shell.Result{Stdout: "\n"}, nil
	}
	name := c.Argv[slices.Index(c.Argv, "--name")+1]
	return shell.Result{Stdout: map[string]string{"/sentry": sentryValue, "/dd-api": ddAPIValue, "/dd-app": ddAppValue}[name] + "\n"}, nil
}

type seen struct {
	mu       sync.Mutex
	requests []*http.Request
	bodies   []string
}

func (s *seen) all() ([]*http.Request, []string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return slices.Clone(s.requests), slices.Clone(s.bodies)
}

func serve(t *testing.T, routes map[string]string) (*httptest.Server, *seen) {
	t.Helper()
	got := &seen{}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		got.mu.Lock()
		got.requests = append(got.requests, r)
		got.bodies = append(got.bodies, string(body))
		got.mu.Unlock()
		said, found := routes[r.URL.Path]
		if !found {
			http.Error(w, `{"detail":"not found"}`, http.StatusNotFound)
			return
		}
		_, _ = io.WriteString(w, said)
	}))
	t.Cleanup(server.Close)
	return server, got
}

func settings(api string) Settings {
	var s Settings
	s.SSM.Profile, s.SSM.Region = "core-auto", "us-west-2"
	s.Sentry.API, s.Sentry.Org, s.Sentry.Token = api+"/sentry", "4505", "/sentry"
	s.Datadog.API, s.Datadog.APIKey, s.Datadog.AppKey = api, "/dd-api", "/dd-app"
	s.Oncall.CLI, s.Oncall.Checkout = "tools/ci oncall", "/drive/checkout"
	return s
}

func newDesk(t *testing.T, runner *fakeRunner, api string) *Desk {
	t.Helper()
	t.Setenv("CCI_HOME", t.TempDir())
	desk, err := NewDesk(context.Background(), runner, settings(api), drive, t.TempDir(), func() time.Time { return started }, io.Discard)
	if err != nil {
		t.Fatal(err)
	}
	return desk
}

func TestAnAskIsAnsweredToItsLaneAndClosedWithNoKeyInTheRecordOrBody(t *testing.T) {
	server, _ := serve(t, map[string]string{
		"/api/v1/monitor/42": `{"name":"api 5xx","type":"query alert","query":"sum:x{*}>1","overall_state":"Alert","state":{"groups":{"service:api":{"status":"Alert","last_triggered_ts":1793908800}}}}`,
	})
	desk := newDesk(t, &fakeRunner{}, server.URL)
	opened, err := client.Post(context.Background(), client.Draft{Drive: drive, Lane: lane, Kind: askdesk.AskKind, Text: "datadog monitor 42", To: []string{Lane}, Force: true})
	if err != nil {
		t.Fatal(err)
	}

	if _, err := desk.loop().Pass(context.Background(), client.Query{Drive: drive, Since: time.Now().Add(-askdesk.CatchUp), Kinds: []string{askdesk.AskKind}}); err != nil {
		t.Fatal(err)
	}

	found, err := client.Tail(context.Background(), client.Query{Drive: drive, Kinds: []string{askdesk.AnswerKind}, Lanes: []string{Lane}})
	if err != nil || len(found) != 1 {
		t.Fatalf("answers = %v, %v", found, err)
	}
	if found[0].Resolves != opened.Seq || found[0].Text != "datadog monitor 42: Alert, 1 groups not OK" {
		t.Fatalf("answer %q resolves %d, want #%d", found[0].Text, found[0].Resolves, opened.Seq)
	}
	body, err := os.ReadFile(found[0].Refs.Path)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(body), "group service:api Alert since 2026-11-05T20:00:00Z") {
		t.Fatalf("body misses the alerting group:\n%s", body)
	}
	raw, _ := json.Marshal(found[0])
	for _, secret := range []string{sentryValue, ddAPIValue, ddAppValue} {
		if strings.Contains(string(raw), secret) || strings.Contains(string(body), secret) {
			t.Fatalf("a key reached the answer")
		}
	}
}

func TestEachKeyReachesOnlyItsOwnServiceHeaders(t *testing.T) {
	server, got := serve(t, map[string]string{
		"/sentry/organizations/4505/issues/7/":               `{"id":"7","shortId":"API-7","status":"unresolved","count":"3"}`,
		"/sentry/organizations/4505/issues/7/events/latest/": `{"eventID":"e1"}`,
		"/api/v1/monitor/9":                                  `{"overall_state":"OK"}`,
	})
	runner := &fakeRunner{}
	desk := newDesk(t, runner, server.URL)

	desk.read(context.Background(), "sentry issue 7")
	desk.read(context.Background(), "datadog monitor 9")

	requests, _ := got.all()
	if len(requests) != 3 {
		t.Fatalf("requests = %d, want 3", len(requests))
	}
	for _, r := range requests[:2] {
		if r.Header.Get("Authorization") != "Bearer "+sentryValue || r.Header.Get("DD-API-KEY") != "" {
			t.Fatalf("a Sentry read carried the wrong headers: %v", r.Header)
		}
	}
	if dd := requests[2]; dd.Header.Get("DD-API-KEY") != ddAPIValue || dd.Header.Get("DD-APPLICATION-KEY") != ddAppValue || dd.Header.Get("Authorization") != "" {
		t.Fatalf("a Datadog read carried the wrong headers: %v", dd.Header)
	}
	for _, call := range runner.calls {
		if !slices.Contains(call.Argv, "--with-decryption") || len(call.Env) != 0 {
			t.Fatalf("an SSM read ran as %v with env %v", call.Argv, call.Env)
		}
	}
}

func TestEveryWriteAndUnknownVerbIsRefusedWithoutACall(t *testing.T) {
	server, got := serve(t, map[string]string{})
	runner := &fakeRunner{}
	desk := newDesk(t, runner, server.URL)
	keyReads := len(runner.calls)

	for _, text := range []string{
		"sentry resolve 7",
		"sentry api https://evil.example/x",
		"datadog mute 42",
		"datadog downtime 42",
		"datadog notebook create",
		"datadog monitor abc",
		"datadog logs",
		"datadog api https://evil.example/x",
		"oncall say inc-1 --kind finding --text hi",
		"oncall connect inc-1 --state x",
		"oncall downtime --monitor 1",
		"oncall page acknowledge p1",
		"oncall takeover inc-1",
		"please read the logs",
	} {
		t.Run(text, func(t *testing.T) {
			read := desk.read(context.Background(), text)
			if !strings.HasPrefix(read.Head, "obs read refused: ") || read.Body == "" {
				t.Fatalf("%q answered %q", text, read.Head)
			}
		})
	}
	if requests, _ := got.all(); len(requests) != 0 || len(runner.calls) != keyReads {
		t.Fatalf("refusals reached %d HTTP calls and %d commands", len(requests), len(runner.calls)-keyReads)
	}
}

func TestAnIssueReadNamesItsStateAndTheLatestStack(t *testing.T) {
	server, _ := serve(t, map[string]string{
		"/sentry/organizations/4505/shortids/API-7A/": `{"groupId":"7"}`,
		"/sentry/organizations/4505/issues/7/": `{"id":"7","shortId":"API-7A","title":"TypeError: x is undefined","culprit":"handler.ts","permalink":"https://sentry.example/issues/7/",` +
			`"status":"unresolved","level":"error","count":"120","userCount":4,"lastSeen":"2026-11-05T19:58:00Z","project":{"slug":"api"}}`,
		"/sentry/organizations/4505/issues/7/events/latest/": `{"eventID":"e1","dateCreated":"2026-11-05T19:58:00Z","title":"TypeError",` +
			`"entries":[{"type":"exception","data":{"values":[{"type":"TypeError","value":"x is undefined","stacktrace":{"frames":[{"filename":"lib.ts","function":"outer","lineNo":3},{"filename":"handler.ts","function":"inner","lineNo":41,"inApp":true}]}}]}}],` +
			`"tags":[{"key":"environment","value":"production"}]}`,
	})
	desk := newDesk(t, &fakeRunner{}, server.URL)

	read := desk.read(context.Background(), "sentry issue API-7A")

	if read.Head != "sentry issue API-7A: API-7A unresolved, 120 events, last 2026-11-05T19:58:00Z" || read.URL != "https://sentry.example/issues/7/" {
		t.Fatalf("head %q url %q", read.Head, read.URL)
	}
	inner, outer := strings.Index(read.Body, "at inner handler.ts:41 in-app"), strings.Index(read.Body, "at outer lib.ts:3")
	if inner < 0 || outer < inner || !strings.Contains(read.Body, "exception TypeError: x is undefined") || !strings.Contains(read.Body, "environment=production") {
		t.Fatalf("body misses the newest frame first, the exception or the tags:\n%s", read.Body)
	}
}

func TestALogsReadSearchesItsWindowNewestFirst(t *testing.T) {
	server, got := serve(t, map[string]string{
		"/api/v2/logs/events/search": `{"data":[{"attributes":{"timestamp":"2026-11-05T19:59:00Z","status":"error","service":"api","host":"h1","message":"boom","attributes":{"error":{"kind":"Timeout","message":"deadline"}}}}]}`,
	})
	desk := newDesk(t, &fakeRunner{}, server.URL)

	read := desk.read(context.Background(), "datadog logs --from 6h service:api status:error")

	requests, bodies := got.all()
	if len(requests) != 1 || requests[0].Method != http.MethodPost {
		t.Fatalf("requests = %v", requests)
	}
	var sent struct {
		Filter struct{ Query, From, To string }
		Sort   string
	}
	if err := json.Unmarshal([]byte(bodies[0]), &sent); err != nil {
		t.Fatal(err)
	}
	if sent.Filter.Query != "service:api status:error" || sent.Filter.From != "2026-11-05T14:00:00Z" || sent.Filter.To != "2026-11-05T20:00:00Z" || sent.Sort != "-timestamp" {
		t.Fatalf("searched %+v", sent)
	}
	if !strings.Contains(read.Body, "2026-11-05T19:59:00Z error api h1: boom {\"error\":{\"kind\":\"Timeout\",\"message\":\"deadline\"}}") {
		t.Fatalf("body = %q", read.Body)
	}
}

func TestAMetricReadListsEachSeriesPoints(t *testing.T) {
	server, got := serve(t, map[string]string{
		"/api/v1/query": `{"status":"ok","series":[{"metric":"trace.http.request.errors","scope":"service:api","pointlist":[[1793908800000,2],[1793908860000,null]]}]}`,
	})
	desk := newDesk(t, &fakeRunner{}, server.URL)

	read := desk.read(context.Background(), "datadog metric sum:trace.http.request.errors{service:api}.as_count()")

	requests, _ := got.all()
	if query := requests[0].URL.Query(); query.Get("query") != "sum:trace.http.request.errors{service:api}.as_count()" || query.Get("from") != "1793905200" || query.Get("to") != "1793908800" {
		t.Fatalf("queried %v", query)
	}
	if read.Body != "trace.http.request.errors{service:api}: 2 points\n  2026-11-05T20:00:00Z 2\n" {
		t.Fatalf("body = %q", read.Body)
	}
}

func TestAnOncallReadRunsTheCheckoutsCLIWithTheSSMProfile(t *testing.T) {
	runner := &fakeRunner{}
	desk := newDesk(t, runner, "http://unused")

	read := desk.read(context.Background(), "oncall incidents --json")

	call := runner.calls[len(runner.calls)-1]
	if !slices.Equal(call.Argv, []string{"tools/ci", "oncall", "incidents", "--json"}) || call.Dir != "/drive/checkout" || !slices.Equal(call.Env, []string{"AWS_PROFILE=core-auto"}) {
		t.Fatalf("ran %v in %s with %v", call.Argv, call.Dir, call.Env)
	}
	if read.Body != "incident inc-1 open\n" {
		t.Fatalf("body = %q", read.Body)
	}
}

func TestManyLanesAskingOneReadCostOneCallWithinAMinute(t *testing.T) {
	server, got := serve(t, map[string]string{"/api/v1/monitor/42": `{"overall_state":"OK"}`})
	desk := newDesk(t, &fakeRunner{}, server.URL)
	now := started
	desk.Now = func() time.Time { return now }

	desk.read(context.Background(), "datadog monitor 42")
	desk.read(context.Background(), "please: datadog monitor 42")
	now = now.Add(2 * time.Minute)
	desk.read(context.Background(), "datadog monitor 42")

	if requests, _ := got.all(); len(requests) != 2 {
		t.Fatalf("requests = %d, want one per minute", len(requests))
	}
}

func TestAFailedReadSaysItsStatusAndKeepsTheBodyOutOfTheLine(t *testing.T) {
	server, _ := serve(t, map[string]string{})
	desk := newDesk(t, &fakeRunner{}, server.URL)

	read := desk.read(context.Background(), "datadog monitor 404")

	if read.Head != "datadog monitor 404: the read answered HTTP 404" || !strings.Contains(read.Body, "not found") {
		t.Fatalf("head %q body %q", read.Head, read.Body)
	}
}

func TestADeskStartRefusesAKeySSMPrintsEmpty(t *testing.T) {
	_, err := NewDesk(context.Background(), &fakeRunner{empty: true}, settings("http://unused"), drive, t.TempDir(), time.Now, io.Discard)
	if err == nil || !strings.Contains(err.Error(), "SSM printed nothing for /sentry") {
		t.Fatalf("err = %v", err)
	}
}

func TestSettingsNameEveryKeyAndTheOncallCheckout(t *testing.T) {
	path := filepath.Join(t.TempDir(), "obs.json")
	if err := os.WriteFile(path, []byte(`{"ssm":{"profile":"core-auto","region":"us-west-2"},"sentry":{"api":"https://sentry.io/api/0","org":"4505"}}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadSettings(path); err == nil || !strings.Contains(err.Error(), "sentry.token") {
		t.Fatalf("err = %v", err)
	}
}
