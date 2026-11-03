package deskcreds

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	DefaultPort   = 9181
	secretPrefix  = "secret/"
	awakeTask     = "desk-creds"
	awakeEvery    = time.Minute
	fetchTimeout  = 10 * time.Second
	headerTimeout = 10 * time.Second
	stderrLimit   = 300
	expiration    = "Expiration"
)

var (
	credentialFields = []string{"Version", "AccessKeyId", "SecretAccessKey", "SessionToken", expiration}
	secretFields     = []string{"Value", expiration}
)

type Held = map[string]json.RawMessage

type server struct {
	mu   sync.Mutex
	held map[string]Held
	now  func() time.Time
	log  io.Writer
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
	s := &server{held: map[string]Held{}, now: now, log: log}
	return s
}

func (s *server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	key := strings.Trim(r.URL.Path, "/")
	status := http.StatusMethodNotAllowed
	switch r.Method {
	case http.MethodGet:
		status = s.get(w, key)
	case http.MethodPut:
		status = s.put(w, r, key)
	default:
		w.WriteHeader(status)
	}
	_, _ = fmt.Fprintf(s.log, "desk-creds: %s /%s %d\n", r.Method, key, status)
}

func (s *server) get(w http.ResponseWriter, key string) int {
	s.mu.Lock()
	credentials := s.held[key]
	s.mu.Unlock()
	if credentials == nil || expired(credentials, s.now()) {
		http.Error(w, "no unexpired credentials were pushed for this profile", http.StatusNotFound)
		return http.StatusNotFound
	}
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
	defer s.mu.Unlock()
	s.held[key] = kept
	w.WriteHeader(http.StatusNoContent)
	return http.StatusNoContent
}

func holdAwake(ctx context.Context, runner shell.Runner, log io.Writer) {
	for {
		_, err := runner.Run(ctx, shell.Command{Argv: []string{"/.sprite/bin/sprite-env", "curl", "-sSf", "-X", "PUT", "/v1/tasks/" + awakeTask, "-H", "Content-Type: application/json", "-d", `{"expire": "5m"}`}, Timeout: awakeEvery})
		if err != nil {
			_, _ = fmt.Fprintf(log, "desk-creds: the %s task was not refreshed: %v\n", awakeTask, err)
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(awakeEvery):
		}
	}
}

func Serve(ctx context.Context, runner shell.Runner, port int, log io.Writer) error {
	go holdAwake(ctx, runner, log)
	listener, err := (&net.ListenConfig{}).Listen(ctx, "tcp", net.JoinHostPort("127.0.0.1", strconv.Itoa(port)))
	if err != nil {
		return err
	}
	return (&http.Server{Handler: Handler(time.Now, log), ReadHeaderTimeout: headerTimeout}).Serve(listener)
}

func Fetch(ctx context.Context, key string, port int) (Held, error) {
	ctx, cancel := context.WithTimeout(ctx, fetchTimeout)
	defer cancel()
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, fmt.Sprintf("http://127.0.0.1:%d/%s", port, key), http.NoBody)
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
		_, err = fmt.Fprintln(out, text)
		return err
	}
	_, err = fmt.Fprintln(out, string(value))
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
