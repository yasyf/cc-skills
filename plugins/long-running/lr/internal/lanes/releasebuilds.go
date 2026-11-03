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
	buildFailed = []string{"failing", "failed"}
	buildClosed = []string{"passed", "canceled", "canceling", "skipped", "not_run"}
	jobFailed   = []string{"failed", "timed_out"}
)

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
	URL       string    `json:"web_url"`
	StartedAt time.Time `json:"started_at"`
	Jobs      []struct {
		Name       string `json:"name"`
		StepKey    string `json:"step_key"`
		State      string `json:"state"`
		SoftFailed bool   `json:"soft_failed"`
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
	var builds []releaseBuild
	for _, slug := range r.Pipelines {
		org, pipeline, _ := strings.Cut(slug, "/")
		path := fmt.Sprintf("/organizations/%s/pipelines/%s/builds?per_page=%d", org, pipeline, buildsRead)
		result, err := r.Env.Runner.Run(ctx, shell.Command{Argv: append(slices.Clone(r.CLI), "api", path), Dir: r.Env.Config.Checkout, Timeout: readTimeout})
		if err != nil {
			return nil, err
		}
		var read []releaseBuild
		if err := json.Unmarshal([]byte(result.Stdout), &read); err != nil {
			return nil, err
		}
		for _, build := range read {
			build.pipeline = slug
			builds = append(builds, build)
		}
	}
	return builds, nil
}

func (r *ReleaseBuilds) tend(ctx context.Context, build releaseBuild, now time.Time) error {
	link := buildLink.FindString(build.URL)
	lane, named := r.named[link]
	age := now.Sub(build.StartedAt)
	if !build.StartedAt.IsZero() && !named && !slices.Contains(buildClosed, build.State) && age >= seenGrace && age < seenWindow {
		text := fmt.Sprintf("RELEASE-SEEN %d %s %s: no record names %s %d minutes after it started at %s; claim it or name it in a release record", build.Number, build.pipeline, build.State, build.URL, int(age.Minutes()), pacific(build.StartedAt))
		if err := r.post(ctx, "release-seen:"+link, []string{Root}, text); err != nil {
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
	return r.post(ctx, "release-failed:"+link+":"+build.State, to, text)
}

func (b releaseBuild) failingStep() string {
	for _, job := range b.Jobs {
		if job.SoftFailed || !slices.Contains(jobFailed, job.State) {
			continue
		}
		if job.StepKey != "" {
			return job.StepKey
		}
		return job.Name
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
