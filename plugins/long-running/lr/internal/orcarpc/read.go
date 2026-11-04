package orcarpc

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const cliBinary = "orca"

var ErrUnsupported = errors.New("orca-go: not a supported read verb")

func (c *Client) Read(ctx context.Context, args []string) (Response, error) {
	flags := flag.NewFlagSet("orca-go", flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	runID := flags.String("run", "", "")
	dispatch := flags.String("dispatch", "", "")
	terminal := flags.String("terminal", "", "")
	worktree := flags.String("worktree", "", "")
	cursor := flags.String("cursor", "", "")
	terminalState := flags.String("terminal-state", "", "")
	limit := flags.Int("limit", 0, "")
	includeRemote := flags.Bool("include-remote", false, "")
	peek := flags.Bool("peek", false, "")
	all := flags.Bool("all", false, "")
	types := flags.String("types", "", "")
	full := flags.Bool("full", false, "")
	asJSON := flags.Bool("json", false, "")

	path, rest := verbPath(args)
	if err := flags.Parse(rest); err != nil {
		return Response{}, fmt.Errorf("%w: %w", ErrUnsupported, err)
	}
	if !*asJSON {
		return Response{}, fmt.Errorf("%w: --json is required", ErrUnsupported)
	}
	switch path {
	case "orchestration worker-list":
		if !*full || *runID == "" {
			return Response{}, fmt.Errorf("%w: worker-list needs --run and --full", ErrUnsupported)
		}
		return c.WorkerList(ctx, WorkerListParams{Run: *runID, TerminalState: *terminalState, IncludeRemote: *includeRemote, Cursor: *cursor, Limit: *limit})
	case "orchestration worker-show":
		if !*full || *dispatch == "" {
			return Response{}, fmt.Errorf("%w: worker-show needs --dispatch and --full", ErrUnsupported)
		}
		return c.WorkerShow(ctx, *dispatch)
	case "orchestration check":
		if !*full || (*peek == *all) {
			return Response{}, fmt.Errorf("%w: check needs --full and one of --peek or --all", ErrUnsupported)
		}
		return c.CheckRead(ctx, CheckParams{Terminal: *terminal, Run: *runID, Peek: *peek, All: *all, Types: *types})
	case "terminal list":
		return c.TerminalList(ctx, TerminalListParams{Worktree: *worktree, Limit: *limit})
	case "terminal show":
		if *terminal == "" {
			return Response{}, fmt.Errorf("%w: terminal show needs --terminal", ErrUnsupported)
		}
		return c.TerminalShow(ctx, *terminal)
	case "worktree show":
		if *worktree == "" {
			return Response{}, fmt.Errorf("%w: worktree show needs --worktree", ErrUnsupported)
		}
		return c.WorktreeShow(ctx, *worktree)
	case "status":
		return c.Status(ctx)
	}
	return Response{}, fmt.Errorf("%w: %s", ErrUnsupported, path)
}

func verbPath(args []string) (string, []string) {
	end := 0
	for end < len(args) && !strings.HasPrefix(args[end], "-") {
		end++
	}
	return strings.Join(args[:end], " "), args[end:]
}

type Runner struct {
	Client *Client
	Next   shell.Runner
}

func (r Runner) Run(ctx context.Context, c shell.Command) (shell.Result, error) {
	if c.Argv[0] != cliBinary || c.Stdin != "" || c.Stdout != nil || (len(c.Env) > 0 && isCheck(c.Argv)) {
		return r.Next.Run(ctx, c)
	}
	read := ctx
	if c.Timeout > 0 {
		var cancel context.CancelFunc
		read, cancel = context.WithTimeout(ctx, c.Timeout)
		defer cancel()
	}
	reply, err := r.Client.Read(read, c.Argv[1:])
	if err != nil || !reply.OK {
		return r.Next.Run(ctx, c)
	}
	out, err := Render(reply)
	if err != nil {
		return r.Next.Run(ctx, c)
	}
	return shell.Result{Stdout: string(out)}, nil
}

func isCheck(argv []string) bool {
	return len(argv) > 2 && argv[1] == "orchestration" && argv[2] == "check"
}
