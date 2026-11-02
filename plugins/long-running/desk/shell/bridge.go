package shell

import (
	"bufio"
	"encoding/json"
	"fmt"
	"net"
	"os"
	"time"
)

// BridgeEnv names the unix socket a test harness serves its Shell on; when set, the desk runs on a Bridge.
const BridgeEnv = "LONG_RUNNING_BRIDGE"

// ExitRaised is the exit code of a desk whose bridged Shell raised an exception nothing in the desk caught,
// or whose bridged Reexec returned; the harness re-raises the exception it recorded.
const ExitRaised = 86

// Raised is an exception the bridged Shell raised, carried as a panic so the desk handles it where Python's would.
type Raised struct {
	Type    string `json:"type"`
	Message string `json:"message"`
}

func (r *Raised) Error() string { return fmt.Sprintf("%s: %s", r.Type, r.Message) }

type wire struct {
	conn   net.Conn
	reader *bufio.Reader
}

// Bridge is the Shell a test harness serves: each call is one JSON request and one JSON reply on the harness's socket,
// dispatched to the live Python Shell, so retries and accounting happen once, wherever that Shell does them.
type Bridge struct {
	socket string
	free   chan *wire
}

// NewBridge returns a Shell served on `socket`.
func NewBridge(socket string) *Bridge {
	return &Bridge{socket: socket, free: make(chan *wire, 64)}
}

func (b *Bridge) call(op string, args map[string]any, result any) {
	var w *wire
	select {
	case w = <-b.free:
	default:
		conn, err := net.Dial("unix", b.socket)
		if err != nil {
			panic(err)
		}
		w = &wire{conn, bufio.NewReaderSize(conn, 1<<20)}
	}
	request := map[string]any{"op": op}
	for name, value := range args {
		request[name] = value
	}
	encoded, err := json.Marshal(request)
	if err != nil {
		panic(err)
	}
	if _, err := w.conn.Write(append(encoded, '\n')); err != nil {
		panic(err)
	}
	line, err := w.reader.ReadBytes('\n')
	if err != nil {
		panic(err)
	}
	select {
	case b.free <- w:
	default:
		w.conn.Close()
	}
	var reply struct {
		Result json.RawMessage `json:"result"`
		Raised *Raised         `json:"raised"`
	}
	if err := json.Unmarshal(line, &reply); err != nil {
		panic(err)
	}
	if reply.Raised != nil {
		panic(reply.Raised)
	}
	if result != nil {
		if err := json.Unmarshal(reply.Result, result); err != nil {
			panic(err)
		}
	}
}

func (b *Bridge) Run(argv []string, stdin *string, env map[string]string, cwd string) Done {
	var done Done
	b.call("run", map[string]any{"argv": argv, "stdin": stdin, "env": env, "cwd": optional(cwd)}, &done)
	return done
}

func (b *Bridge) CciQuery(argv []string, env map[string]string, cwd string) Done {
	var done Done
	b.call("cci_query", map[string]any{"argv": argv, "env": env, "cwd": optional(cwd)}, &done)
	return done
}

func optional(text string) *string {
	if text == "" {
		return nil
	}
	return &text
}

func (b *Bridge) PassCost() string {
	var cost string
	b.call("pass_cost", nil, &cost)
	return cost
}

func (b *Bridge) InFlight() (string, float64, bool) {
	var flight *struct {
		Name    string  `json:"name"`
		Seconds float64 `json:"seconds"`
	}
	b.call("in_flight", nil, &flight)
	if flight == nil {
		return "", 0, false
	}
	return flight.Name, flight.Seconds, true
}

func (b *Bridge) Adopt(values map[string]string) {
	b.call("adopt", map[string]any{"values": values}, nil)
}

// Reexec hands the re-execution to the harness's Shell and ends this process, since a real re-exec never returns.
func (b *Bridge) Reexec(argv []string) {
	defer os.Exit(ExitRaised)
	b.call("reexec", map[string]any{"argv": argv}, nil)
}

func (b *Bridge) Launchctl(args ...string) Done {
	var done Done
	b.call("launchctl", map[string]any{"args": args}, &done)
	return done
}

type bridged struct {
	bridge *Bridge
	handle int
	pid    int
}

func (p *bridged) Pid() int { return p.pid }

func (p *bridged) Poll() (int, bool) {
	var code *int
	p.bridge.call("poll", map[string]any{"handle": p.handle}, &code)
	if code == nil {
		return 0, false
	}
	return *code, true
}

func (b *Bridge) Spawn(argv []string, out string, env map[string]string, appending bool) Process {
	var spawned struct {
		Handle int `json:"handle"`
		Pid    int `json:"pid"`
	}
	b.call("spawn", map[string]any{"argv": argv, "out": out, "env": env, "append": appending}, &spawned)
	return &bridged{b, spawned.Handle, spawned.Pid}
}

func (b *Bridge) RunsJob(pid int, directory string) bool {
	var runs bool
	b.call("runs_job", map[string]any{"pid": pid, "directory": directory}, &runs)
	return runs
}

func (b *Bridge) Stat(path string) (string, bool) {
	var stat *string
	b.call("stat", map[string]any{"path": path}, &stat)
	if stat == nil {
		return "", false
	}
	return *stat, true
}

func (b *Bridge) Now() time.Time {
	var now string
	b.call("now", nil, &now)
	parsed, err := time.Parse(time.RFC3339Nano, now)
	if err != nil {
		panic(err)
	}
	return parsed
}

func (b *Bridge) Monotonic() float64 {
	var seconds float64
	b.call("monotonic", nil, &seconds)
	return seconds
}

func (b *Bridge) Sleep(seconds float64) {
	b.call("sleep", map[string]any{"seconds": seconds}, nil)
}

// Wait waits in real time, as Python's threading.Event.wait does under the harness's fake clock.
func (b *Bridge) Wait(stop <-chan struct{}, seconds float64) bool {
	select {
	case <-stop:
		return true
	case <-time.After(time.Duration(seconds * float64(time.Second))):
		return false
	}
}

func (b *Bridge) Load() float64 {
	var load float64
	b.call("load", nil, &load)
	return load
}

func (b *Bridge) Cores() int {
	var cores int
	b.call("cores", nil, &cores)
	return cores
}

func (b *Bridge) Env(name string) string {
	var value string
	b.call("env", map[string]any{"name": name}, &value)
	return value
}

func (b *Bridge) Getenv(name string) string {
	var value string
	b.call("getenv", map[string]any{"name": name}, &value)
	return value
}

func (b *Bridge) Pid() int {
	var pid int
	b.call("pid", nil, &pid)
	return pid
}

func (b *Bridge) Stdout(text string) {
	b.call("write", map[string]any{"stream": "stdout", "text": text}, nil)
}

func (b *Bridge) Stderr(text string) {
	b.call("write", map[string]any{"stream": "stderr", "text": text}, nil)
}
