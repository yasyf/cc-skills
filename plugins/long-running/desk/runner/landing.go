package runner

// Landing is the Go port of desk-runner.py's Landing; each method still to port panics naming the Python it ports.
type Landing struct {
	runner *Runner
	config map[string]any
}

// NewLanding is Landing.__init__ (desk-runner.py L5982).
func NewLanding(r *Runner, config map[string]any) *Landing {
	return &Landing{runner: r, config: config}
}

// Run is Landing.run (L10295).
func (l *Landing) Run() { panic("TODO: port Landing.run (desk-runner.py L10295): ask_rulings, cci_tail.remember, gate_pass, pass-cleanup") }

// Drain is Landing.drain (L6550).
func (l *Landing) Drain() { panic("TODO: port Landing.drain (desk-runner.py L6550)") }

// PostState is Landing.post_state (L9363).
func (l *Landing) PostState(took float64) { panic("TODO: port Landing.post_state (desk-runner.py L9363)") }

// Close is Landing.close (L6521).
func (l *Landing) Close() {}

// KeepFresh is Landing.keep_fresh (L10232).
func (l *Landing) KeepFresh() { panic("TODO: port Landing.keep_fresh (desk-runner.py L10232)") }

// KeepOverflowing is Landing.keep_overflowing (L9341).
func (l *Landing) KeepOverflowing() { panic("TODO: port Landing.keep_overflowing (desk-runner.py L9341)") }
