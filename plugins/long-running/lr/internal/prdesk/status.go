package prdesk

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"io/fs"
	"net"
	"net/http"
	"os"
	"strconv"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const socketFile = "pr-desk.sock"

type Why struct {
	State   store.PRState
	Holds   []store.Hold
	Carries []store.Carry
	Owed    []store.Owed
}

func listen(path string) (net.Listener, error) {
	if err := os.Remove(path); err != nil && !errors.Is(err, fs.ErrNotExist) {
		return nil, err
	}
	return net.Listen("unix", path)
}

func statusServer(s *store.Store, repo string) *http.Server {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /status", func(w http.ResponseWriter, _ *http.Request) {
		var rows []store.PRState
		answer(w, s.Update(func(tx *store.Tx) (err error) {
			rows, err = tx.States(repo)
			return err
		}), rows)
	})
	mux.HandleFunc("GET /why/{number}", func(w http.ResponseWriter, r *http.Request) {
		number, err := strconv.Atoi(r.PathValue("number"))
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		var why Why
		var found bool
		err = s.Update(func(tx *store.Tx) (err error) {
			if why.State, found, err = tx.State(repo, number); err != nil || !found {
				return err
			}
			if why.Holds, err = tx.Holds(repo, number); err != nil {
				return err
			}
			if why.Carries, err = tx.Carries(repo, number); err != nil {
				return err
			}
			why.Owed, err = tx.OwedFor(repo, number)
			return err
		})
		if err == nil && !found {
			http.Error(w, fmt.Sprintf("the PR desk has no row for #%d", number), http.StatusNotFound)
			return
		}
		answer(w, err, why)
	})
	return &http.Server{Handler: mux, ReadHeaderTimeout: 5 * time.Second}
}

func answer(w http.ResponseWriter, err error, body any) {
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(body)
}

func ask(ctx context.Context, path, route string, into any) error {
	client := http.Client{Transport: &http.Transport{DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
		var dialer net.Dialer
		return dialer.DialContext(ctx, "unix", path)
	}}}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, "http://pr-desk"+route, nil)
	if err != nil {
		return err
	}
	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("no PR desk is answering at %s: %w", path, err)
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		said, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("the PR desk answered %s: %s", resp.Status, said)
	}
	return json.NewDecoder(resp.Body).Decode(into)
}
