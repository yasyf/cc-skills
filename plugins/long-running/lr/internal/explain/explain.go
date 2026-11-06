package explain

import (
	"bytes"
	"cmp"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"html/template"
	"io"
	"io/fs"
	"net/http"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-inbox/client"
	"github.com/yuin/goldmark"
	"github.com/yuin/goldmark/ast"
	"github.com/yuin/goldmark/extension"
	"github.com/yuin/goldmark/text"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/drive"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/launchd"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	Label      = "com.long-running.explain"
	Branch     = "explainers"
	Port       = 8664
	ServePort  = 8665
	Topic      = "lr-explain"
	relay      = "owner-relay"
	answerWait = 30
	stepWait   = time.Minute
)

var (
	nonWord  = regexp.MustCompile(`[^a-z0-9]+`)
	record   = regexp.MustCompile(`^#\d+(@\w+)?$`)
	markdown = goldmark.New(goldmark.WithExtensions(extension.GFM))
	page     = template.Must(template.New("page").Parse(`<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{.Title}}</title>
<meta name="description" content="{{.Lede}}">
<meta name="updated" content="{{.Updated.Format "2006-01-02"}}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&display=swap">
<link rel="stylesheet" href="../kit/one-pager.css">
<script type="module" src="../kit/one-pager.js"></script>
<script type="module" src="../../architecture/kit/arch-doc.js"></script>
<style>arch-doc{display:block;--smap-max-h:70vh}figure.op-fig:has(arch-doc){margin:1.5rem 0}</style>
</head>
<body>
<header class="op-head">
  <div class="op-bar"><a class="op-home" href="../">← All one-pagers</a></div>
  <h1>{{.Title}}</h1>
{{- with .Lede}}
  <p class="xs-sub">{{.}}</p>
{{- end}}
  <ul class="op-nav">
{{- range .Sections}}{{if .ID}}
    <li><a href="#{{.ID}}">{{.Heading}}</a></li>
{{- end}}{{end}}
  </ul>
</header>
{{range .Sections}}
<section class="op-panel"{{with .ID}} id="{{.}}"{{end}}>
{{- with .Heading}}
  <header><h2>{{.}}</h2></header>
{{- end}}
{{.Body}}
</section>
{{end}}
<footer class="op-foot">Updated {{.Updated.Format "January 2, 2006"}}.</footer>
</body>
</html>
`))
)

func Slug(topic string) string {
	return strings.Trim(nonWord.ReplaceAllString(strings.ToLower(topic), "-"), "-")
}

type section struct {
	ID      string
	Heading string
	Body    template.HTML
}

type onePager struct {
	Title    string
	Lede     string
	Updated  time.Time
	Sections []section
}

func parse(topic string, source []byte, updated time.Time) (onePager, error) {
	doc := markdown.Parser().Parse(text.NewReader(source))
	p := onePager{Updated: updated}
	var body bytes.Buffer
	heading := ""
	flush := func() {
		if heading != "" || strings.TrimSpace(body.String()) != "" {
			p.Sections = append(p.Sections, section{ID: Slug(heading), Heading: heading, Body: template.HTML(body.String())}) //nolint:gosec // goldmark escapes raw HTML in the source, and an arch block is base64 below.
		}
		body.Reset()
	}
	for node := doc.FirstChild(); node != nil; node = node.NextSibling() {
		opening := p.Sections == nil && heading == "" && body.Len() == 0
		switch n := node.(type) {
		case *ast.Heading:
			switch {
			case n.Level == 1 && opening && p.Title == "" && p.Lede == "":
				p.Title = plain(n, source)
				continue
			case n.Level == 2:
				flush()
				heading = plain(n, source)
				continue
			}
		case *ast.Paragraph:
			if opening && p.Lede == "" {
				p.Lede = plain(n, source)
				continue
			}
		case *ast.FencedCodeBlock:
			if string(n.Language(source)) == archLanguage {
				body.WriteString(`<figure class="op-fig"><arch-doc src="data:application/json;base64,` + base64.StdEncoding.EncodeToString(fence(n, source)) + "\" fill></arch-doc></figure>\n")
				continue
			}
		}
		if err := markdown.Renderer().Render(&body, source, node); err != nil {
			return onePager{}, err
		}
	}
	flush()
	if p.Title == "" {
		p.Title = topic
	}
	return p, nil
}

func (p onePager) HTML() ([]byte, error) {
	var out bytes.Buffer
	if err := page.Execute(&out, p); err != nil {
		return nil, err
	}
	return out.Bytes(), nil
}

func plain(n ast.Node, source []byte) string {
	var out strings.Builder
	_ = ast.Walk(n, func(n ast.Node, entering bool) (ast.WalkStatus, error) {
		if t, ok := n.(*ast.Text); ok && entering {
			out.Write(t.Segment.Value(source))
			if t.SoftLineBreak() {
				out.WriteByte(' ')
			}
		}
		return ast.WalkContinue, nil
	})
	return out.String()
}

type Checkout struct {
	Runner shell.Runner
	Remote string
	Dir    string
}

func Locate(ctx context.Context, runner shell.Runner, repo, claude string) (Checkout, error) {
	root := filepath.Join(claude, "worktrees")
	configured, err := runner.Run(ctx, shell.Command{Argv: []string{"git", "config", "ccx.worktreeRoot"}, Timeout: stepWait})
	var exit *shell.ExitError
	switch {
	case err == nil:
		root = strings.TrimSpace(configured.Stdout)
	case !errors.As(err, &exit) || exit.Code != 1:
		return Checkout{}, err
	}
	return Checkout{
		Runner: runner,
		Remote: "https://github.com/" + path.Dir(repo) + "/design-docs.git",
		Dir:    filepath.Join(root, "design-docs", Branch),
	}, nil
}

func (c Checkout) git(ctx context.Context, dir string, args ...string) (string, error) {
	result, err := c.Runner.Run(ctx, shell.Command{Argv: append([]string{"git", "-C", dir}, args...), Timeout: stepWait})
	return result.Stdout, err
}

func (c Checkout) Write(ctx context.Context, topic, slug string, body []byte) (bool, error) {
	if _, err := os.Stat(c.Dir); errors.Is(err, fs.ErrNotExist) {
		if err := c.add(ctx); err != nil {
			return false, err
		}
	} else if err != nil {
		return false, err
	}
	file := filepath.Join(c.Dir, "one-pagers", slug, "index.html")
	if err := os.MkdirAll(filepath.Dir(file), 0o750); err != nil {
		return false, err
	}
	if err := os.WriteFile(file, body, 0o600); err != nil {
		return false, err
	}
	changed, err := c.git(ctx, c.Dir, "status", "--porcelain", "--", file)
	if err != nil || changed == "" {
		return false, err
	}
	if _, err := c.git(ctx, c.Dir, "add", "--", file); err != nil {
		return false, err
	}
	_, err = c.git(ctx, c.Dir, "commit", "--quiet", "--no-verify", "-m", "one-pagers: explain "+topic, "--", file)
	return true, err
}

func (c Checkout) add(ctx context.Context) error {
	if _, err := c.Runner.Run(ctx, shell.Command{Argv: []string{"git", "clone", "--quiet", "--depth", "1", "--branch", "main", c.Remote, c.Dir}}); err != nil {
		return err
	}
	_, err := c.git(ctx, c.Dir, "switch", "--quiet", "-c", Branch)
	return err
}

type Host interface {
	Serve(ctx context.Context, dir string) (string, error)
}

type Tailnet struct {
	Runner  shell.Runner
	Agents  launchd.Launchd
	Binary  string
	Log     string
	Answers func(string) bool
}

func (t Tailnet) Serve(ctx context.Context, dir string) (string, error) {
	backend := fmt.Sprintf("127.0.0.1:%d", Port)
	job := launchd.Job{Label: Label, Arguments: []string{t.Binary, "explain", "serve", "--dir", dir, "--addr", backend}, Log: t.Log}
	if err := t.Agents.Install(ctx, job); err != nil {
		return "", err
	}
	if _, err := t.Runner.Run(ctx, shell.Command{Argv: []string{"tailscale", "serve", "--bg", fmt.Sprintf("--https=%d", ServePort), "http://" + backend}, Timeout: stepWait}); err != nil {
		return "", err
	}
	status, err := t.Runner.Run(ctx, shell.Command{Argv: []string{"tailscale", "status", "--peers=false", "--json"}, Timeout: stepWait})
	if err != nil {
		return "", err
	}
	var self struct {
		Self struct {
			DNSName string `json:"DNSName"`
		} `json:"Self"`
	}
	if err := json.Unmarshal([]byte(status.Stdout), &self); err != nil || self.Self.DNSName == "" {
		return "", errors.New("tailscale status --json names no DNSName for this machine")
	}
	url := fmt.Sprintf("https://%s:%d/", strings.TrimSuffix(self.Self.DNSName, "."), ServePort)
	for range answerWait {
		if t.Answers(url) {
			return url, nil
		}
		t.Agents.Sleep(time.Second)
	}
	return "", fmt.Errorf("%s runs but %s did not answer within %d s; see %s", Label, url, answerWait, t.Log)
}

type Notice struct {
	Title string
	Lede  string
	Link  string
}

type Announcer interface {
	Announce(ctx context.Context, n Notice) error
	Failed(ctx context.Context, topic string, cause error) error
}

type Owner struct {
	Runner shell.Runner
	Post   func(context.Context, client.Draft) (client.Record, error)
	Desk   func() (string, error)
	Drive  string
	Lane   string
}

func (o Owner) Announce(ctx context.Context, n Notice) error {
	draft := client.Draft{
		Drive: o.Drive,
		Lane:  cmp.Or(o.Lane, Topic),
		Kind:  "report",
		Text:  fmt.Sprintf("one-pager for the owner, %s: %s", n.Title, n.Link),
		Attrs: map[string]string{"one-pager-url": n.Link, "one-pager-title": n.Title, "one-pager-lede": n.Lede},
	}
	if o.Lane != relay {
		draft.To = []string{relay}
	}
	if _, err := o.Post(ctx, draft); err != nil {
		return err
	}
	err := o.dm(ctx, n)
	if err == nil {
		return nil
	}
	if _, posted := o.Post(ctx, client.Draft{Drive: o.Drive, Lane: o.Lane, Kind: "blocker", To: []string{"root"}, Text: fmt.Sprintf("lr explain hosted %s but could not post it at the owner's Slack DM root: %v", n.Link, err)}); posted != nil {
		return errors.Join(err, posted)
	}
	return fmt.Errorf("the owner's Slack DM did not get %s, and a blocker to root says so: %w", n.Link, err)
}

type unsent struct{ Owner }

func (unsent) Announce(context.Context, Notice) error {
	return nil
}

func (o Owner) Failed(ctx context.Context, topic string, cause error) error {
	desk, err := o.Desk()
	if err != nil {
		return errors.Join(cause, err)
	}
	if _, err := o.Post(ctx, client.Draft{Drive: o.Drive, Lane: o.Lane, Kind: "defect", To: []string{desk}, Topic: Topic, Text: fmt.Sprintf("lr explain could not host its page on %s: %v", topic, cause)}); err != nil {
		return errors.Join(cause, err)
	}
	return fmt.Errorf("no page for %s, and a defect to %s says so: %w", topic, desk, cause)
}

func (o Owner) dm(ctx context.Context, n Notice) error {
	card, err := json.Marshal(map[string]any{"title": n.Title, "body": n.Lede, "links": []map[string]string{{"label": "Open the one-pager", "url": n.Link}}})
	if err != nil {
		return err
	}
	_, err = o.Runner.Run(ctx, shell.Command{Argv: []string{"cc-slack", "dm-status", "--card", string(card)}, Timeout: stepWait})
	return err
}

func Explain(ctx context.Context, checkout Checkout, host Host, owner Announcer, topic string, source []byte, now time.Time) (string, error) {
	if claims := Uncited(source); len(claims) > 0 {
		return "", refuse(claims)
	}
	if err := refuseDiagrams(source); err != nil {
		return "", err
	}
	notice, _, err := publish(ctx, checkout, host, topic, Visible(source), now)
	if err != nil {
		return "", owner.Failed(ctx, topic, err)
	}
	return notice.Link, owner.Announce(ctx, notice)
}

func publish(ctx context.Context, checkout Checkout, host Host, topic string, source []byte, now time.Time) (Notice, bool, error) {
	slug := Slug(topic)
	if slug == "" {
		return Notice{}, false, fmt.Errorf("topic %q has no letter or digit to name its page", topic)
	}
	p, err := parse(topic, source, now)
	if err != nil {
		return Notice{}, false, err
	}
	body, err := p.HTML()
	if err != nil {
		return Notice{}, false, err
	}
	changed, err := checkout.Write(ctx, topic, slug, body)
	if err != nil {
		return Notice{}, false, err
	}
	base, err := host.Serve(ctx, checkout.Dir)
	if err != nil {
		return Notice{}, false, err
	}
	return Notice{Title: p.Title, Lede: p.Lede, Link: base + "one-pagers/" + slug + "/"}, changed, nil
}

func read(ctx context.Context, runner shell.Runner, from string) ([]byte, error) {
	if !record.MatchString(from) {
		return os.ReadFile(from)
	}
	result, err := runner.Run(ctx, shell.Command{Argv: []string{"cci", "show", from, "--json"}, Timeout: stepWait})
	if err != nil {
		return nil, err
	}
	var shown struct {
		Text string `json:"text"`
		Refs struct {
			Path string `json:"path"`
		} `json:"refs"`
	}
	if err := json.Unmarshal([]byte(result.Stdout), &shown); err != nil {
		return nil, fmt.Errorf("cci show %s: %w", from, err)
	}
	if shown.Refs.Path == "" {
		return []byte(shown.Text), nil
	}
	return os.ReadFile(shown.Refs.Path)
}

func answers(url string) bool {
	client := http.Client{Timeout: 5 * time.Second}
	response, err := client.Get(url)
	if err != nil {
		return false
	}
	_, _ = io.Copy(io.Discard, response.Body)
	_ = response.Body.Close()
	return response.StatusCode == http.StatusOK
}

func metaDesk(name string) (string, error) {
	entry, err := drive.Named(name)
	if err != nil {
		return "", err
	}
	registry, err := store.Open(entry.Store())
	if err != nil {
		return "", err
	}
	defer func() { _ = registry.Close() }()
	return lanes.MetaDesk(registry)
}

func publisher(cmd *cobra.Command, a *app.App, cfg config.Config) (Checkout, Tailnet, Owner, error) {
	claude, err := drive.ConfigDir()
	if err != nil {
		return Checkout{}, Tailnet{}, Owner{}, err
	}
	agents, err := launchd.New(a.Runner, cmd.ErrOrStderr())
	if err != nil {
		return Checkout{}, Tailnet{}, Owner{}, err
	}
	checkout, err := Locate(cmd.Context(), a.Runner, cfg.Repo, claude)
	if err != nil {
		return Checkout{}, Tailnet{}, Owner{}, err
	}
	home := filepath.Join(claude, "long-running")
	return checkout,
		Tailnet{Runner: a.Runner, Agents: agents, Binary: filepath.Join(home, "current", "bin", "lr"), Log: filepath.Join(home, "explain.log"), Answers: answers},
		Owner{Runner: a.Runner, Post: client.Post, Desk: func() (string, error) { return metaDesk(cfg.Drive) }, Drive: cfg.Drive, Lane: os.Getenv(lanes.LaneEnv)},
		nil
}

func Command(a *app.App) *cobra.Command {
	var from string
	var noSend bool
	cmd := &cobra.Command{
		Use:   "explain <topic>",
		Short: "Write a one-pager on a topic into this machine's design-docs explainers branch, host it on the tailnet, and send its link to the owner",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			cfg, err := a.Config()
			if err != nil {
				return err
			}
			source, err := read(cmd.Context(), a.Runner, from)
			if err != nil {
				return err
			}
			checkout, host, owner, err := publisher(cmd, a, cfg)
			if err != nil {
				return err
			}
			var announcer Announcer = owner
			if noSend {
				announcer = unsent{owner}
			}
			link, err := Explain(cmd.Context(), checkout, host, announcer, args[0], source, a.Now())
			if link != "" {
				fmt.Fprintln(cmd.OutOrStdout(), link)
			}
			return err
		},
	}
	cmd.Flags().StringVar(&from, "from", "", "a markdown file, or a cci record such as #359589 whose text or body is markdown")
	cmd.Flags().BoolVar(&noSend, "no-send", false, "write and host the page and print its link, with no Slack card to the owner and no record to owner-relay")
	_ = cmd.MarkFlagRequired("from")
	cmd.AddCommand(redesignCommand(a))
	var dir, addr string
	serve := &cobra.Command{Use: "serve", Hidden: true, Short: "Serve the explainers worktree, as the launchd agent lr explain installs", Args: cobra.NoArgs, RunE: func(*cobra.Command, []string) error {
		server := http.Server{Addr: addr, Handler: http.FileServer(http.Dir(dir)), ReadHeaderTimeout: 10 * time.Second}
		return server.ListenAndServe()
	}}
	serve.Flags().StringVar(&dir, "dir", "", "the explainers worktree")
	serve.Flags().StringVar(&addr, "addr", "", "the loopback address tailscale serve forwards to")
	cmd.AddCommand(serve)
	return cmd
}
