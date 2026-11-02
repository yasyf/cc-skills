package runner

import "sync"

// OnePassIO is pass_io.OnePassIO: with landing.trace_pass_io, each named phase of the first pass logs its IO once.
type OnePassIO struct {
	mu      sync.Mutex
	enabled bool
	seen    map[string]bool
}

// Finish is OnePassIO.finish.
func (p *OnePassIO) Finish() {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.enabled = false
}

// Phase is OnePassIO.phase around `body`.
func (p *OnePassIO) Phase(name string, body func()) {
	p.mu.Lock()
	selected := !p.seen[name] && p.enabled
	if selected {
		p.seen[name] = true
	}
	p.mu.Unlock()
	if selected {
		panic("TODO: port pass_io's PASS-IO capture before a runner.json sets landing.trace_pass_io for the Go desk")
	}
	body()
}
