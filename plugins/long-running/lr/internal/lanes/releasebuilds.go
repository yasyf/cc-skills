package lanes

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"regexp"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	seenGrace   = 5 * time.Minute
	seenWindow  = time.Hour
	buildsEvery = time.Minute
	buildsRead  = 20
)

var (
	buildLink   = regexp.MustCompile(`buildkite\.com/[\w.-]+/[\w.-]+/builds/\d+`)
	buildFailed = []string{"FAILING", "FAILED"}
	jobEnded    = []string{"FINISHED", "TIMED_OUT"}
)

const releaseBuildFields = `fragment releaseBuild on Build {
  number state url startedAt
  jobs(first: 100, type: COMMAND) { edges { node { ... on JobTypeCommand { label state passed softFailed step { key } } } } }
}`

type ReleaseBuilds struct {
	Env       Env
	Pipelines []string
	CLI       []string
	Log       *slog.Logger
	read      time.Time
	after     int64
	named     map[string]string
	posted    map[string]bool
}

type releaseBuild struct {
	Number    int       `json:"number"`
	State     string    `json:"state"`
	URL       string    `json:"url"`
	StartedAt time.Time `json:"startedAt"`
	Jobs      struct {
		Edges []struct {
			Node struct {
				Label      string `json:"label"`
				State      string `json:"state"`
				Passed     bool   `json:"passed"`
				SoftFailed bool   `json:"softFailed"`
				Step       *struct {
					Key string `json:"key"`
				} `json:"step"`
			} `json:"node"`
		} `json:"edges"`
	} `json:"jobs"`
	pipeline string
}

func (r *ReleaseBuilds) Check(ctx context.Context, now time.Time) error {
	if !r.read.IsZero() && now.Sub(r.read) < buildsEvery {
		return nil
	}
	r.read = now
	if err := r.readNames(ctx, now); err != nil {
		r.Log.Warn("release builds did not read the drive", "err", err)
		return nil
	}
	builds, err := r.builds(ctx)
	if err != nil {
		r.Log.Warn("release builds did not read Buildkite", "err", err)
		return nil
	}
	for _, build := range builds {
		if err := r.tend(ctx, build, now); err != nil {
			return err
		}
	}
	return nil
}

func (r *ReleaseBuilds) readNames(ctx context.Context, now time.Time) error {
	query := client.Query{Drive: r.Env.Config.Drive, After: r.after}
	if r.after == 0 {
		query.Since = now.Add(-gateBack)
	}
	rows, err := r.Env.Bus.Tail(ctx, query)
	if err != nil {
		return err
	}
	if r.named == nil {
		r.named = map[string]string{}
	}
	for _, row := range rows {
		r.after = max(r.after, row.Seq)
		if row.Lane == Self {
			continue
		}
		said := strings.Join(append([]string{row.Text, row.Topic, row.Refs.URL}, row.Refs.Builds...), " ")
		for _, link := range buildLink.FindAllString(said, -1) {
			if owner, seen := r.named[link]; !seen || owner == Root {
				r.named[link] = row.Lane
			}
		}
	}
	return nil
}

func (r *ReleaseBuilds) builds(ctx context.Context) ([]releaseBuild, error) {
	variables := map[string]string{}
	params := make([]string, 0, len(r.Pipelines))
	fields := make([]string, 0, len(r.Pipelines))
	for i, slug := range r.Pipelines {
		variables[fmt.Sprintf("p%d", i)] = slug
		params = append(params, fmt.Sprintf("$p%d: ID!", i))
		fields = append(fields, fmt.Sprintf("p%d: pipeline(slug: $p%d) { builds(first: %d) { edges { node { ...releaseBuild } } } }", i, i, buildsRead))
	}
	bound, err := json.Marshal(variables)
	if err != nil {
		return nil, err
	}
	query := fmt.Sprintf("query(%s) { %s }\n%s", strings.Join(params, ", "), strings.Join(fields, " "), releaseBuildFields)
	argv := append(slices.Clone(r.CLI), "graphql", "-v", string(bound), query)
	result, err := r.Env.Runner.Run(ctx, shell.Command{Argv: argv, Dir: r.Env.Config.Checkout, Timeout: readTimeout})
	if err != nil {
		return nil, err
	}
	var answered map[string]struct {
		Builds struct {
			Edges []struct {
				Node releaseBuild `json:"node"`
			} `json:"edges"`
		} `json:"builds"`
	}
	if err := json.Unmarshal([]byte(result.Stdout), &answered); err != nil {
		return nil, err
	}
	var builds []releaseBuild
	for i, slug := range r.Pipelines {
		for _, edge := range answered[fmt.Sprintf("p%d", i)].Builds.Edges {
			build := edge.Node
			build.pipeline = slug
			builds = append(builds, build)
		}
	}
	return builds, nil
}

func (r *ReleaseBuilds) tend(ctx context.Context, build releaseBuild, now time.Time) error {
	link := buildLink.FindString(build.URL)
	state := strings.ToLower(build.State)
	lane, named := r.named[link]
	age := now.Sub(build.StartedAt)
	if !build.StartedAt.IsZero() && !named && age >= seenGrace && age < seenWindow {
		text := fmt.Sprintf("RELEASE-SEEN %d %s %s: no record names %s %d minutes after it started at %s; claim it or name it in a release record", build.Number, build.pipeline, state, build.URL, int(age.Minutes()), pacific(build.StartedAt))
		if err := r.post(ctx, "release-seen:"+link+":"+state, []string{Root}, text); err != nil {
			return err
		}
	}
	if !slices.Contains(buildFailed, build.State) || build.StartedAt.IsZero() || age >= gateBack {
		return nil
	}
	to := []string{Root}
	if named && lane != Root {
		to = append(to, lane)
	}
	text := fmt.Sprintf("RELEASE-FAILED %d %s %s", build.Number, build.failingStep(), build.URL)
	return r.post(ctx, "release-failed:"+link+":"+state, to, text)
}

func (b releaseBuild) failingStep() string {
	for _, edge := range b.Jobs.Edges {
		job := edge.Node
		if job.Passed || job.SoftFailed || !slices.Contains(jobEnded, job.State) {
			continue
		}
		if job.Step != nil && job.Step.Key != "" {
			return job.Step.Key
		}
		return job.Label
	}
	return "(no command step failed)"
}

func (r *ReleaseBuilds) post(ctx context.Context, key string, to []string, text string) error {
	if r.posted[key] {
		return nil
	}
	if _, err := r.Env.Bus.Post(ctx, client.Draft{Drive: r.Env.Config.Drive, Lane: Self, Kind: blockerKind, To: to, ID: key, Stamp: true, Text: text}); err != nil {
		return fmt.Errorf("%w: cci post: %w", ErrUnread, err)
	}
	if r.posted == nil {
		r.posted = map[string]bool{}
	}
	r.posted[key] = true
	return nil
}
