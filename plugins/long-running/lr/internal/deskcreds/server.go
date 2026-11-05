package deskcreds

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"os"
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/awake"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	DefaultPort   = 9181
	secretPrefix  = "secret/"
	awakeTask     = "desk-creds"
	fetchTimeout  = 10 * time.Second
	headerTimeout = 10 * time.Second
	stderrLimit   = 300
	expiration    = "Expiration"
	PushLane      = "desk-creds-push"
	serveLane     = "desk-creds"
	askKind       = "ask"
	EscalateAfter = 2 * time.Minute
)

var (
	credentialFields = []string{"Version", "AccessKeyId", "SecretAccessKey", "SessionToken", expiration}
	secretFields     = []string{"Value", expiration}
)

type Held = map[string]json.RawMessage

type server struct {
	mu      sync.Mutex
	held    map[string]Held
	changed chan struct{}
	now     func() time.Time
	log     io.Writer
}

func expired(credentials Held, now time.Time) bool {
	var text string
	if err := json.Unmarshal(credentials[expiration], &text); err != nil {
		return true
	}
	at, err := time.Parse(time.RFC3339, text)
	return err != nil || !at.After(now)
}

func Handler(now func() time.Time, log io.Writer) http.Handler {
	s := &server{held: map[string]Held{}, changed: make(chan struct{}), now: now, log: log}
	return s
}

func (s *server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	key := strings.Trim(r.URL.Path, "/")
	status := http.StatusMethodNotAllowed
	switch r.Method {
	case http.MethodGet:
		status = s.get(w, r, key)
	case http.MethodPut:
		status = s.put(w, r, key)
	default:
		w.WriteHeader(status)
	}
	_, _ = fmt.Fprintf(s.log, "desk-creds: %s /%s %d\n", r.Method, key, status)
}

func (s *server) get(w http.ResponseWriter, r *http.Request, key string) int {
	wait := r.URL.Query().Has("wait")
	for {
		s.mu.Lock()
		credentials, changed := s.held[key], s.changed
		s.mu.Unlock()
		if credentials != nil && !expired(credentials, s.now()) {
			return s.answer(w, credentials)
		}
		if !wait {
			http.Error(w, "no unexpired credentials were pushed for this profile", http.StatusNotFound)
			return http.StatusNotFound
		}
		select {
		case <-changed:
		case <-r.Context().Done():
			return http.StatusRequestTimeout
		}
	}
}

func (s *server) answer(w http.ResponseWriter, credentials Held) int {
	body, err := json.Marshal(credentials)
	if err != nil {
		http.Error(w, "the held credentials did not encode", http.StatusInternalServerError)
		return http.StatusInternalServerError
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(body)
	return http.StatusOK
}

func (s *server) put(w http.ResponseWriter, r *http.Request, key string) int {
	fields := credentialFields
	if strings.HasPrefix(key, secretPrefix) {
		fields = secretFields
	}
	refuse := func() int {
		http.Error(w, fmt.Sprintf("%s needs %s and an Expiration still ahead", key, strings.Join(fields, ", ")), http.StatusBadRequest)
		return http.StatusBadRequest
	}
	var given Held
	if err := json.NewDecoder(r.Body).Decode(&given); err != nil {
		return refuse()
	}
	kept := make(Held, len(fields))
	for _, field := range fields {
		value, found := given[field]
		if !found {
			return refuse()
		}
		kept[field] = value
	}
	if expired(kept, s.now()) {
		return refuse()
	}
	s.mu.Lock()
	s.held[key] = kept
	close(s.changed)
	s.changed = make(chan struct{})
	s.mu.Unlock()
	w.WriteHeader(http.StatusNoContent)
	return http.StatusNoContent
}

func Serve(ctx context.Context, runner shell.Runner, port int, drive string, log io.Writer) error {
	go awake.Holder{Runner: runner, Task: awakeTask, Every: awake.Every, Log: slog.New(slog.NewTextHandler(log, nil)), Wanted: awake.Always}.Run(ctx)
	listener, err := (&net.ListenConfig{}).Listen(ctx, "tcp", net.JoinHostPort("127.0.0.1", strconv.Itoa(port)))
	if err != nil {
		return err
	}
	go func() {
		if err := AskForPush(ctx, drive, time.Now()); err != nil {
			_, _ = fmt.Fprintf(log, "desk-creds: the ask for a push did not post: %v\n", err)
		}
	}()
	return (&http.Server{Handler: Handler(time.Now, log), ReadHeaderTimeout: headerTimeout}).Serve(listener)
}

func AskForPush(ctx context.Context, drive string, started time.Time) error {
	host, err := os.Hostname()
	if err != nil {
		return err
	}
	_, err = client.Post(ctx, client.Draft{
		Drive: drive, Lane: serveLane, Kind: askKind, To: []string{PushLane}, Force: true,
		ID:   fmt.Sprintf("desk-creds-start/%s/%d", host, started.UnixNano()),
		Text: fmt.Sprintf("desk-creds on %s started holding nothing: push its credentials", host),
	})
	return err
}

func Fetch(ctx context.Context, key string, port int) (Held, error) {
	ctx, cancel := context.WithTimeout(ctx, fetchTimeout)
	defer cancel()
	return fetch(ctx, fmt.Sprintf("http://127.0.0.1:%d/%s", port, key))
}

func fetch(ctx context.Context, url string) (Held, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, url, http.NoBody)
	if err != nil {
		return nil, err
	}
	answer, err := http.DefaultClient.Do(request)
	if err != nil {
		return nil, err
	}
	defer func() { _ = answer.Body.Close() }()
	if answer.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("HTTP %d", answer.StatusCode)
	}
	var held Held
	if err := json.NewDecoder(answer.Body).Decode(&held); err != nil {
		return nil, err
	}
	return held, nil
}

func Get(ctx context.Context, profile, field string, port int, out io.Writer) error {
	held, err := Fetch(ctx, profile, port)
	if err != nil {
		return fmt.Errorf("desk-creds get: no credentials for %s on port %d: %w", profile, port, err)
	}
	return printHeld(held, profile, field, out)
}

func Wait(ctx context.Context, profile, field string, port int, drive string, patience time.Duration, log, out io.Writer) error {
	held, err := Fetch(ctx, profile, port)
	if err == nil {
		return printHeld(held, profile, field, out)
	}
	host, err := os.Hostname()
	if err != nil {
		return err
	}
	_, _ = fmt.Fprintf(log, "desk-creds get: waiting on %s, which desk-creds on port %d does not hold\n", profile, port)
	url := fmt.Sprintf("http://127.0.0.1:%d/%s?wait", port, profile)
	patient, cancel := context.WithTimeout(ctx, patience)
	held, err = fetch(patient, url)
	cancel()
	if err != nil {
		if _, err := client.Post(ctx, client.Draft{
			Drive: drive, Lane: serveLane, Kind: "blocker", To: []string{"root"}, Force: true,
			ID:   fmt.Sprintf("desk-creds-wait/%s/%s/%d", host, profile, os.Getpid()),
			Text: fmt.Sprintf("A service on %s is stopped waiting on %s from desk-creds, which nothing has pushed in %s; the Mac's lr desk-creds keep pushes it", host, profile, patience),
		}); err != nil {
			return err
		}
		if held, err = fetch(ctx, url); err != nil {
			return fmt.Errorf("desk-creds get: no credentials for %s on port %d: %w", profile, port, err)
		}
	}
	return printHeld(held, profile, field, out)
}

func printHeld(held Held, profile, field string, out io.Writer) error {
	if field == "" {
		encoded, err := json.Marshal(held)
		if err != nil {
			return err
		}
		_, err = fmt.Fprintln(out, string(encoded))
		return err
	}
	value, found := held[field]
	if !found {
		return fmt.Errorf("desk-creds get: %s holds no field %s", profile, field)
	}
	var text string
	if json.Unmarshal(value, &text) == nil {
		_, err := fmt.Fprintln(out, text)
		return err
	}
	_, err := fmt.Fprintln(out, string(value))
	return err
}

func sortedKeys[V any](items map[string]V) []string {
	keys := make([]string, 0, len(items))
	for key := range items {
		keys = append(keys, key)
	}
	slices.Sort(keys)
	return keys
}
