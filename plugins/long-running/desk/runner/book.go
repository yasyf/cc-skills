package runner

// SeedPolicy is seed_policy (desk-runner.py L10413).
func (r *Runner) SeedPolicy() { panic("TODO: port seed_policy on the Go store (Runner.landing_policy L3302, accept_policy L3315)") }

// Flush is Runner.flush (L2203).
func (r *Runner) Flush() bool { panic("TODO: port Runner.flush on the Go store (L2203)") }

// TailFailed is Runner.tail_failed (L2729).
func (r *Runner) TailFailed(failure CciTailFailed) { panic("TODO: port Runner.tail_failed on the Go store (L2729)") }

// OrcaDown is Runner.orca_down (L2160).
func (r *Runner) OrcaDown(down OrcaUnreachable) { panic("TODO: port Runner.orca_down on the Go store (L2160)") }
