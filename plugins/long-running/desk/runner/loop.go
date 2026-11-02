package runner

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
)

// PluginRootEnv names the long-running plugin's root, whose .claude-plugin/plugin.json holds the version this desk runs.
const PluginRootEnv = "LONG_RUNNING_PLUGIN_ROOT"

// CciTailFailed is desk-runner.py's CciTailFailed.
type CciTailFailed struct{ Message string }

func (e CciTailFailed) Error() string { return e.Message }

// OrcaUnreachable is desk-runner.py's OrcaUnreachable.
type OrcaUnreachable struct{ Message string }

func (e OrcaUnreachable) Error() string { return e.Message }

type outcome struct {
	failure any
	stack   []byte
}

func attempt(body func()) (result outcome) {
	defer func() {
		if failure := recover(); failure != nil {
			result = outcome{failure, stack()}
		}
	}()
	body()
	return outcome{}
}

// RunLanding is run_landing.
func RunLanding(r *Runner, once bool) int {
	r.SeedPolicy()
	landing := NewLanding(r, r.Config.Landing)
	defer landing.Close()
	if !once {
		landing.KeepFresh()
		landing.KeepOverflowing()
	}
	interval := float64(DefaultSeconds)
	if seconds, ok := number(r.Config.Landing["interval_seconds"]); ok {
		interval = seconds
	}
	alive := r.KeepAlive("landing")
	defer close(alive)
	for {
		started := r.Now()
		r.Passing["landing"] = true
		result := attempt(func() {
			landing.Run()
			if once {
				landing.Drain()
			}
		})
		switch failure := result.failure.(type) {
		case nil:
		case StopRequestedError:
			r.PassIO.Finish()
			landing.Close()
			r.Flush()
			removeMarker(r.StopMarker())
			return 0
		case CciTailFailed:
			r.TailFailed(failure)
		case OrcaUnreachable:
			r.OrcaDown(failure)
		default:
			if r.PassRaised("landing", failure, result.stack) {
				r.Shell.Sleep(RaisedBackoff.Seconds())
				landing.Close()
				r.Reexec("landing")
			}
		}
		delete(r.Passing, "landing")
		behind := r.Moved()
		took := r.Now().Sub(started).Seconds()
		r.PassIO.Phase("post-state", func() { landing.PostState(took) })
		r.PassIO.Phase("flush-and-beat", func() {
			r.Flush()
			r.Shell.Stderr(fmt.Sprintf("PASS landing %d on %s took %.0fs: %s\n", r.Passes["landing"]+1, r.Version, took, r.Shell.PassCost()))
			r.Beat("landing")
		})
		r.PassIO.Finish()
		if once {
			return 0
		}
		if r.Rest(max(0, interval-r.Now().Sub(started).Seconds())) {
			landing.Close()
			removeMarker(r.StopMarker())
			return 0
		}
		if behind {
			landing.Close()
			r.Reexec("landing")
		}
	}
}

func removeMarker(path string) {
	if err := os.Remove(path); err != nil && !errors.Is(err, fs.ErrNotExist) {
		panic(err)
	}
}
