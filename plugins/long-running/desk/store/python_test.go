package store

import (
	"bufio"
	"bytes"
	"context"
	"database/sql/driver"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"math/rand/v2"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/desk/pyjson"
)

func moduleRoot(t *testing.T) string {
	t.Helper()
	dir, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	for !isFile(filepath.Join(dir, "go.mod")) {
		parent := filepath.Dir(dir)
		if parent == dir {
			t.Fatal("no go.mod above the test's working directory")
		}
		dir = parent
	}
	if !isFile(filepath.Join(filepath.Dir(dir), "pyproject.toml")) {
		t.Fatalf("%s has no pyproject.toml beside the desk module", filepath.Dir(dir))
	}
	return dir
}

func fixture(t *testing.T, args ...string) *exec.Cmd {
	t.Helper()
	uv, err := exec.LookPath("uv")
	if err != nil {
		t.Fatalf("the store's Python round trips run through uv, which is not on PATH: %v", err)
	}
	module := moduleRoot(t)
	return exec.Command(uv, append([]string{"run", "--locked", "--project", filepath.Dir(module), "python", filepath.Join(module, "testdata", "store_fixture.py")}, args...)...)
}

func runFixture(t *testing.T, stdin io.Reader, args ...string) (string, error) {
	t.Helper()
	command := fixture(t, args...)
	var stdout, stderr bytes.Buffer
	command.Stdin, command.Stdout, command.Stderr = stdin, &stdout, &stderr
	if err := command.Run(); err != nil {
		return stdout.String(), fmt.Errorf("store_fixture.py %s: %w\n%s%s", strings.Join(args, " "), err, stdout.String(), stderr.String())
	}
	return stdout.String(), nil
}

func containerView(t *testing.T, ctx context.Context, store *Store, container string) string {
	t.Helper()
	facts, err := store.Facts(ctx, container, nil)
	if err != nil {
		t.Fatal(err)
	}
	actions, err := store.Actions(ctx, container)
	if err != nil {
		t.Fatal(err)
	}
	records := []any{}
	for _, action := range actions {
		records = append(records, action.fields())
	}
	view, err := pyjson.Dumps([]any{facts, records})
	if err != nil {
		t.Fatal(err)
	}
	return view
}

func databaseRows(t *testing.T, ctx context.Context, path string) map[string][][]driver.Value {
	t.Helper()
	c, err := dial(ctx, path, "ro")
	if err != nil {
		t.Fatal(err)
	}
	defer c.close()
	rows := map[string][][]driver.Value{}
	schema, err := c.rows(ctx, "SELECT type, name, tbl_name, rootpage, sql FROM sqlite_schema ORDER BY name")
	if err != nil {
		t.Fatal(err)
	}
	rows["sqlite_schema"] = schema
	tables, err := c.rows(ctx, "SELECT name FROM sqlite_schema WHERE type = 'table' ORDER BY name")
	if err != nil {
		t.Fatal(err)
	}
	for _, table := range tables {
		name := table[0].(string)
		columns, err := c.rows(ctx, fmt.Sprintf(`SELECT name FROM pragma_table_info('%s')`, name))
		if err != nil {
			t.Fatal(err)
		}
		order := make([]string, len(columns))
		for index := range columns {
			order[index] = strconv.Itoa(index + 1)
		}
		selected, err := c.rows(ctx, fmt.Sprintf(`SELECT * FROM "%s" ORDER BY %s`, name, strings.Join(order, ", ")))
		if err != nil {
			t.Fatal(err)
		}
		for _, row := range selected {
			for index, column := range columns {
				if name == "release_action_clock" && column[0] == "epoch" {
					row[index] = fmt.Sprintf("blob of %d bytes", len(row[index].([]byte)))
				}
			}
		}
		rows[name] = selected
	}
	for _, pragma := range []string{"user_version", "journal_mode", "page_size", "encoding", "auto_vacuum"} {
		value, err := c.rows(ctx, "PRAGMA "+pragma)
		if err != nil {
			t.Fatal(err)
		}
		rows["pragma "+pragma] = value
	}
	return rows
}

func TestRoundTripWithPython(t *testing.T) {
	ctx := context.Background()
	pythonRoot := filepath.Join(t.TempDir(), "python")
	goRoot := filepath.Join(t.TempDir(), "go")
	written, err := runFixture(t, nil, "write", pythonRoot)
	if err != nil {
		t.Fatal(err)
	}
	seen, err := pyjson.Dumps(runScenario(t, goRoot))
	if err != nil {
		t.Fatal(err)
	}
	if strings.TrimSuffix(written, "\n") != seen {
		t.Fatalf("the Go store observed what Python did not:\npython: %s\ngo:     %s", strings.TrimSuffix(written, "\n"), seen)
	}
	if diff, err := runFixture(t, nil, "check", goRoot); err != nil {
		t.Fatalf("Python reads the Go-written store differently from its own:\n%s\n%v", diff, err)
	}

	python, golang := New(pythonRoot), New(goRoot)
	for _, container := range []string{runner, landing, lane} {
		if want, got := containerView(t, ctx, golang, container), containerView(t, ctx, python, container); got != want {
			t.Errorf("Go reads %s from the Python store as\n%s\nand from its own as\n%s", container, got, want)
		}
	}
	numbers, err := python.Fact(ctx, lane, "numbers")
	if err != nil {
		t.Fatal(err)
	}
	if got, _ := pyjson.Dumps(numbers); got != `{"list": [99, 2, 1], "int": 4, "nested": "flattened", "empty": {"now": "filled"}}` {
		t.Errorf("Python's numbers fact reads as %s", got)
	}
	orca, err := python.Action(ctx, runner, "escalation:orca:2026-11-02T08:01:00Z")
	if err != nil {
		t.Fatal(err)
	}
	if orca.Target != "ORCA-UNREACHABLE orca:2026-11-02T08:01:00Z runner: Orca gave no answer — curl: (7) ñ 🚀; since 12:01 AM PST the runner reads no dispatch as gone" || orca.Status != "completed" || *orca.StartedAt != "2026-11-02T08:02:00Z" {
		t.Errorf("Python's escalation reads as %+v", orca)
	}
	if stale, err := python.Action(ctx, landing, "policy:r3"); err != nil || stale.Status != "failed" || *stale.Reason != "names r2 predecessor; the accepted landing policy is ready-prefix at r1 (owner)" {
		t.Errorf("Python's stale policy reads as %+v, %v", stale, err)
	}

	for _, root := range []string{pythonRoot, goRoot} {
		names, err := filepath.Glob(filepath.Join(root, "*.sqlite3"))
		if err != nil || len(names) != 3 {
			t.Fatalf("%s holds databases %v, %v", root, names, err)
		}
	}
	for _, container := range []string{runner, landing, lane} {
		want := databaseRows(t, ctx, filepath.Join(pythonRoot, container+".sqlite3"))
		got := databaseRows(t, ctx, filepath.Join(goRoot, container+".sqlite3"))
		for table, rows := range want {
			if !reflect.DeepEqual(got[table], rows) {
				t.Errorf("%s %s differs:\npython: %v\ngo:     %v", container, table, rows, got[table])
			}
		}
		if len(got) != len(want) {
			t.Errorf("%s has tables %d in Go and %d in Python", container, len(got), len(want))
		}
	}
}

type served struct {
	t      *testing.T
	in     io.WriteCloser
	out    *bufio.Reader
	stderr *bytes.Buffer
}

func serve(t *testing.T, root string) *served {
	t.Helper()
	command := fixture(t, "serve", root)
	in, err := command.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	out, err := command.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	var stderr bytes.Buffer
	command.Stderr = &stderr
	if err := command.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		in.Close()
		command.Wait()
	})
	return &served{t: t, in: in, out: bufio.NewReader(out), stderr: &stderr}
}

func (p *served) send(command ...any) any {
	p.t.Helper()
	line, err := json.Marshal(command)
	if err != nil {
		p.t.Fatal(err)
	}
	if _, err := p.in.Write(append(line, '\n')); err != nil {
		p.t.Fatal(err)
	}
	reply, err := p.out.ReadString('\n')
	if err != nil {
		p.t.Fatalf("store_fixture.py serve stopped answering %v: %v\n%s", command, err, p.stderr.String())
	}
	value, err := pyjson.Loads(strings.TrimSuffix(reply, "\n"))
	if err != nil {
		p.t.Fatal(err)
	}
	return value
}

func (p *served) quit() {
	p.t.Helper()
	if _, err := p.in.Write([]byte("[\"quit\"]\n")); err != nil {
		p.t.Fatal(err)
	}
}

func (p *served) expect(want string, command ...any) {
	p.t.Helper()
	if got := p.send(command...); got != want {
		p.t.Fatalf("%v answered %v, not %s", command, got, want)
	}
}

func (p *served) facts(container string) *pyjson.Object {
	p.t.Helper()
	facts, _ := p.send("read", container).(*pyjson.Object).Get("facts")
	return facts.(*pyjson.Object)
}

type edited struct {
	value any
	err   error
}

func TestConcurrentWithPython(t *testing.T) {
	ctx := context.Background()
	root := t.TempDir()
	book := NewBook(New(root), &fixedClock{at: start})
	if err := book.Ensure(ctx, runner, runner); err != nil {
		t.Fatal(err)
	}
	python := serve(t, root)
	done := make(chan edited, 1)
	pending := func(what string) {
		t.Helper()
		select {
		case result := <-done:
			t.Fatalf("%s: %+v", what, result)
		case <-time.After(500 * time.Millisecond):
		}
	}

	python.expect("held", "hold", runner, "holder", "python")
	go func() {
		value, err := book.Edit(ctx, runner, func(tx *Transaction) (any, error) {
			facts, err := tx.Facts()
			if err != nil {
				return nil, err
			}
			holder, err := facts.Get("holder")
			if err != nil {
				return nil, err
			}
			return holder, facts.Set("after", holder)
		})
		done <- edited{value, err}
	}()
	pending("Go's edit ran while Python held the incident's transaction")
	python.expect("committed", "commit")
	if result := <-done; result.err != nil || result.value != "python" {
		t.Fatalf("Go's edit after Python's commit read %+v", result)
	}

	python.expect("held", "holdraw", runner)
	go func() {
		_, err := book.Edit(ctx, runner, func(tx *Transaction) (any, error) {
			facts, err := tx.Facts()
			if err != nil {
				return nil, err
			}
			return nil, facts.Set("busy", "waited")
		})
		done <- edited{nil, err}
	}()
	pending("Go's BEGIN IMMEDIATE passed Python's SQLite write lock")
	python.expect("committed", "commitraw")
	if result := <-done; result.err != nil {
		t.Fatalf("Go's edit behind Python's write lock failed: %v", result.err)
	}

	inside, release := make(chan struct{}), make(chan struct{})
	go func() {
		_, err := book.Edit(ctx, runner, func(tx *Transaction) (any, error) {
			facts, err := tx.Facts()
			if err != nil {
				return nil, err
			}
			if err := facts.Set("go", "pending"); err != nil {
				return nil, err
			}
			close(inside)
			<-release
			return nil, nil
		})
		done <- edited{nil, err}
	}()
	select {
	case <-inside:
	case result := <-done:
		t.Fatalf("Go's held edit failed: %v", result.err)
	}
	if before := python.facts(runner); before.Has("go") || !before.Has("after") || !before.Has("busy") {
		t.Errorf("Python read %v while Go held an uncommitted write", before.Keys())
	}
	close(release)
	if result := <-done; result.err != nil {
		t.Fatal(result.err)
	}
	if after, _ := python.facts(runner).Get("go"); after != "pending" {
		t.Errorf("Python read Go's committed fact as %v", after)
	}

	err := book.Store.Reading(ctx, func(ctx context.Context) error {
		_, version, err := book.Store.ActionsWithVersion(ctx, runner)
		if err != nil {
			return err
		}
		if same, err := book.Store.Unchanged(ctx, runner, version); !same || err != nil {
			t.Errorf("the store reads as changed with nobody writing: %v, %v", same, err)
		}
		python.expect("held", "hold", runner, "python-again", "yes")
		python.expect("committed", "commit")
		if same, err := book.Store.Unchanged(ctx, runner, version); same || err != nil {
			t.Errorf("Python's commit left Go's data_version unchanged: %v, %v", same, err)
		}
		again, err := book.Fact(ctx, runner, "python-again")
		if err != nil || again != "yes" {
			t.Errorf("Go's cached reader read Python's commit as %v, %v", again, err)
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	python.quit()
}

func TestPythonOracle(t *testing.T) {
	texts := []string{
		`{"b":1,"a":2,"b":3}`, `-0`, `1E400`, `1e-400`, `[1.0, 1e5, -0.0, 12345678901234567890123]`,
		`"\ud83d\ude80"`, `"\ud83d"`, `"\ud83d\u0041"`, `"\ude80\ud83d"`, `"\ud83d\u12zz"`,
		`"ü	tab"`, `{"nested": {"deep": [1, {"x": null}]}, "e": [], "o": {}}`, `"é\u00E9\u0000"`, " [ 1 ,\n2 ] ",
		`"\/\b\f\n\r\t\"\\"`, `NaN`, `-Infinity`, `[Infinity]`, `1.5e+3`, `1.5E-3`, `-9223372036854775808`, `9223372036854775808`,
		`0.1`, `100`, `1e16`, `1e15`, `123456789.125`, `[1e-5, 0.0001]`, strings.Repeat("7", 4300), strings.Repeat("7", 4301),
		"", `{"a" 1}`, `[1,]`, `{"a":1,}`, `"a`, `1 2`, "\"\x01\"", `"\x"`, `"\u12"`, `[1 2]`, `{1:2}`, `-`, "\ufeff1",
		`[`, `{`, `nul`, `01`, "\n\n  x", `["ü", x]`, `{"a": [1, 2}`, `tru`, `1.`, `.5`, `1e`, `-01`, `"\u00zz"`,
	}
	random := rand.New(rand.NewPCG(1, 2))
	values := []float64{0, math.Copysign(0, -1), 1, 0.1, 1e16, 1e15, 1e-5, 1e-4, 5e-324, math.MaxFloat64, math.Inf(1), math.Inf(-1), math.NaN(), 1.0 / 3, 2.0 / 3, 123456789.125, 1e22, 1e23, 9007199254740993}
	for range 300 {
		values = append(values, math.Float64frombits(random.Uint64()))
	}
	for range 200 {
		values = append(values, math.Round(random.Float64()*1e6)/math.Pow(10, float64(random.IntN(12))))
	}
	floats := make([]string, len(values))
	for index, value := range values {
		floats[index] = strconv.FormatUint(math.Float64bits(value), 10)
	}
	stamps := [][]int{
		{2026, 11, 2, 0, 0, 1, 0, -28800}, {2026, 11, 2, 8, 0, 2, 999999, 0}, {2026, 12, 31, 23, 59, 59, 999999, -3600},
		{2026, 1, 1, 0, 30, 0, 0, 3600}, {2024, 2, 29, 12, 0, 0, 0, 19800}, {2000, 1, 1, 0, 0, 0, 0, 86399},
	}
	parses := []string{
		"2026-11-02T08:00:00Z", "2026-11-02T08:00:00.5Z", "2026-11-02T08:00:00.1234567Z", "2026-11-02T08:00:00,25Z",
		"2026-11-02T08:00:00+05:30", "2026-11-02T08:00:00-07:00", "2026-11-02T08:00:00+05:30:15", "2026-11-02 08:00:00Z",
		"2026-11-02T08:00:00+05:60", "2024-02-29T23:59:59Z", "0001-01-01T00:00:00Z", "2026-11-02T08:00:00.Z",
		"2026-11-02T08:00:00ZZ", "2026-02-30T00:00:00Z", "2026-11-02T08:00:60Z", "2026-13-02T08:00:00Z", "2026-11-02T08:00:00+24:00",
	}
	request, err := json.Marshal(map[string]any{"texts": texts, "floats": floats, "stamps": stamps, "parses": parses})
	if err != nil {
		t.Fatal(err)
	}
	out, err := runFixture(t, bytes.NewReader(request), "oracle")
	if err != nil {
		t.Fatal(err)
	}
	var reply struct {
		Texts []struct {
			Error   *string `json:"error"`
			Default string  `json:"default"`
			Compact string  `json:"compact"`
		} `json:"texts"`
		Floats []string `json:"floats"`
		Reprs  []string `json:"reprs"`
		Stamps []string `json:"stamps"`
		Parses []struct {
			Error  *string `json:"error"`
			UTC    string  `json:"utc"`
			Offset int     `json:"offset"`
		} `json:"parses"`
	}
	if err := json.Unmarshal([]byte(out), &reply); err != nil {
		t.Fatalf("%v: %s", err, out)
	}
	for index, text := range texts {
		want := reply.Texts[index]
		value, err := pyjson.Loads(text)
		if want.Error != nil {
			if err == nil || err.Error() != *want.Error {
				t.Errorf("Loads(%q) = %v, %v; Python raises %q", text, value, err, *want.Error)
			}
			continue
		}
		if err != nil {
			t.Errorf("Loads(%q): %v; Python reads it", text, err)
			continue
		}
		dumped, _ := pyjson.Dumps(value)
		compact, _ := pyjson.DumpsCompact(value)
		if dumped != want.Default || compact != want.Compact {
			t.Errorf("%q round-trips as %s and %s; Python writes %s and %s", text, dumped, compact, want.Default, want.Compact)
		}
	}
	for index, value := range values {
		if dumped, _ := pyjson.Dumps(value); dumped != reply.Floats[index] {
			t.Errorf("Dumps(%v) = %s; Python writes %s", value, dumped, reply.Floats[index])
		}
		if repr := pyjson.FloatRepr(value); repr != reply.Reprs[index] {
			t.Errorf("FloatRepr(%v) = %s; Python writes %s", value, repr, reply.Reprs[index])
		}
	}
	for index, fields := range stamps {
		at := time.Date(fields[0], time.Month(fields[1]), fields[2], fields[3], fields[4], fields[5], fields[6]*1000, time.FixedZone("", fields[7]))
		if got := Stamp(at); got != reply.Stamps[index] {
			t.Errorf("Stamp(%v) = %s; Python writes %s", at, got, reply.Stamps[index])
		}
	}
	for index, text := range parses {
		want := reply.Parses[index]
		parsed, err := ParseStamp(text)
		if want.Error != nil {
			if err == nil || strings.HasPrefix(*want.Error, "Invalid isoformat string") && err.Error() != *want.Error {
				t.Errorf("ParseStamp(%q) = %v, %v; Python raises %q", text, parsed, err, *want.Error)
			}
			continue
		}
		if err != nil {
			t.Errorf("ParseStamp(%q): %v; Python reads it", text, err)
			continue
		}
		utc := parsed.UTC()
		iso := utc.Format("2006-01-02T15:04:05")
		if micro := utc.Nanosecond() / 1000; micro != 0 {
			iso += fmt.Sprintf(".%06d", micro)
		}
		_, offset := parsed.Zone()
		if iso+"+00:00" != want.UTC || offset != want.Offset {
			t.Errorf("ParseStamp(%q) = %s at offset %d; Python reads %s at %d", text, iso, offset, want.UTC, want.Offset)
		}
	}
}
