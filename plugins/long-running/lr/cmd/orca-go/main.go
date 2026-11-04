package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/orcarpc"
)

const unsupportedExit = 64

var errUnsupported = errors.New("orca-go: not a supported read verb")

func run(ctx context.Context, client *orcarpc.Client, args []string) (orcarpc.Response, error) {
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
		return orcarpc.Response{}, fmt.Errorf("%w: %w", errUnsupported, err)
	}
	if !*asJSON {
		return orcarpc.Response{}, fmt.Errorf("%w: --json is required", errUnsupported)
	}
	switch path {
	case "orchestration worker-list":
		if !*full || *runID == "" {
			return orcarpc.Response{}, fmt.Errorf("%w: worker-list needs --run and --full", errUnsupported)
		}
		return client.WorkerList(ctx, orcarpc.WorkerListParams{Run: *runID, TerminalState: *terminalState, IncludeRemote: *includeRemote, Cursor: *cursor, Limit: *limit})
	case "orchestration worker-show":
		if !*full || *dispatch == "" {
			return orcarpc.Response{}, fmt.Errorf("%w: worker-show needs --dispatch and --full", errUnsupported)
		}
		return client.WorkerShow(ctx, *dispatch)
	case "orchestration check":
		if !*full || (*peek == *all) {
			return orcarpc.Response{}, fmt.Errorf("%w: check needs --full and one of --peek or --all", errUnsupported)
		}
		return client.CheckRead(ctx, orcarpc.CheckParams{Terminal: *terminal, Run: *runID, Peek: *peek, All: *all, Types: *types})
	case "terminal list":
		return client.TerminalList(ctx, orcarpc.TerminalListParams{Worktree: *worktree, Limit: *limit})
	case "terminal show":
		if *terminal == "" {
			return orcarpc.Response{}, fmt.Errorf("%w: terminal show needs --terminal", errUnsupported)
		}
		return client.TerminalShow(ctx, *terminal)
	case "worktree show":
		if *worktree == "" {
			return orcarpc.Response{}, fmt.Errorf("%w: worktree show needs --worktree", errUnsupported)
		}
		return client.WorktreeShow(ctx, *worktree)
	case "status":
		return client.Status(ctx)
	}
	return orcarpc.Response{}, fmt.Errorf("%w: %s", errUnsupported, path)
}

func verbPath(args []string) (string, []string) {
	end := 0
	for end < len(args) && !strings.HasPrefix(args[end], "-") {
		end++
	}
	return strings.Join(args[:end], " "), args[end:]
}

func main() {
	reply, err := run(context.Background(), orcarpc.New(), os.Args[1:])
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		if errors.Is(err, errUnsupported) {
			os.Exit(unsupportedExit)
		}
		os.Exit(1)
	}
	out, err := orcarpc.Render(reply)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	_, _ = os.Stdout.Write(out)
	if !reply.OK {
		os.Exit(1)
	}
}
