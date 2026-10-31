package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"sync/atomic"
	"time"

	"github.com/yasyf/cc-inbox/internal/cli"
	"github.com/yasyf/cc-inbox/internal/kinds"
	"github.com/yasyf/cc-inbox/internal/store"
)

func must(err error) {
	if err != nil {
		panic(err)
	}
}

func respond(w http.ResponseWriter, value any) {
	w.Header().Set("Content-Type", "application/json")
	must(json.NewEncoder(w).Encode(value))
}

func main() {
	ctx := context.Background()
	home, err := filepath.Abs(os.Args[1])
	must(err)
	population, err := strconv.Atoi(os.Args[2])
	must(err)
	st, err := store.Open(ctx, home)
	must(err)
	defer func() { must(st.Close()) }()
	bodies := filepath.Join(home, "bodies")
	must(os.MkdirAll(bodies, 0700))
	history := make([]store.Ingestion, population)
	for i := range history {
		path := filepath.Join(bodies, fmt.Sprintf("unrelated-%d.txt", i))
		must(os.WriteFile(path, []byte("unrelated incident body without a PR"), 0600))
		history[i] = store.Ingestion{Record: store.Record{Drive: "d1", Lane: "worker", Kind: kinds.Incident, At: time.Now().UTC(), Text: "unrelated incident", Refs: store.Refs{Path: path}, Source: "post"}, LineHash: fmt.Sprintf("incident-index-unrelated-%d", i)}
	}
	inserted, err := st.IngestAll(ctx, history)
	must(err)
	if inserted != population {
		panic(fmt.Errorf("seeded %d incidents, want %d", inserted, population))
	}
	body := filepath.Join(bodies, "selected.txt")
	must(os.WriteFile(body, []byte("body-only #29020!"), 0600))
	selected, _, err := st.Post(ctx, store.Record{ID: "selected-incident", Drive: "d1", Lane: "incident-worker", Kind: kinds.Incident, Text: "fallback #29021", Refs: store.Refs{Path: body, PRs: []store.PR{{Number: 29022}}}}, 0)
	must(err)
	_, err = store.MaintainIndexes(ctx, home)
	must(err)
	_, err = st.ProvisionIncidentScope(ctx, "d1")
	must(err)
	handler, err := cli.IncidentHoldsHandler(ctx, st)
	must(err)
	defer func() { must(handler.Close()) }()
	var requests atomic.Int64
	var newer atomic.Int64
	mux := http.NewServeMux()
	mux.Handle("/v1/incident-holds", http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests.Add(1)
		handler.ServeHTTP(w, r)
	}))
	mux.HandleFunc("GET /fixture/counts", func(w http.ResponseWriter, r *http.Request) {
		stats, err := handler.Stats()
		must(err)
		respond(w, map[string]any{"native_requests": requests.Load(), "backend": stats})
	})
	mux.HandleFunc("POST /fixture/{operation}", func(w http.ResponseWriter, r *http.Request) {
		switch r.PathValue("operation") {
		case "body-unicode-nonmatch":
			must(os.WriteFile(body, []byte("#29020α"), 0600))
		case "body-match":
			must(os.WriteFile(body, []byte("body-only #29020!"), 0600))
		case "body-rename":
			must(os.WriteFile(body+".next", []byte("replacement #29023!"), 0600))
			must(os.Rename(body+".next", body))
		case "body-remove":
			must(os.Remove(body))
		case "body-recreate":
			must(os.WriteFile(body, []byte("recreated #29020!"), 0600))
		case "body-invalid":
			must(os.WriteFile(body, append([]byte("#29020"), 0xff), 0600))
		case "append":
			posted, _, err := st.Post(r.Context(), store.Record{ID: "newer-incident", Drive: "d1", Lane: "incident-worker", Kind: kinds.Incident, Text: "newest #29020"}, 0)
			must(err)
			newer.Store(posted.Seq)
		case "resolve-newer":
			_, _, err := st.Post(r.Context(), store.Record{ID: "resolve-newer", Drive: "d1", Lane: "root", Kind: kinds.Done, Text: "resolved", Resolves: newer.Load()}, 0)
			must(err)
		case "expire-selected":
			db, err := sql.Open("sqlite", filepath.Join(home, store.DBFile))
			must(err)
			_, err = db.ExecContext(r.Context(), "UPDATE records SET expires_at=1 WHERE seq=?", selected.Seq)
			must(err)
			must(db.Close())
		case "unsupported-relative":
			_, _, err := st.Post(r.Context(), store.Record{ID: "unsupported-relative", Drive: "d1", Lane: "worker", Kind: kinds.Incident, Text: "unsupported #29020", Refs: store.Refs{Path: "relative-body.txt"}}, 0)
			must(err)
		default:
			panic(fmt.Errorf("unknown fixture operation %q", r.PathValue("operation")))
		}
		respond(w, map[string]any{"selected": selected.Seq, "newer": newer.Load()})
	})
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	must(err)
	server := &http.Server{Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	done := make(chan error, 1)
	go func() { done <- server.Serve(listener) }()
	must(json.NewEncoder(os.Stdout).Encode(map[string]any{"url": "http://" + listener.Addr().String(), "home": home, "population": inserted, "selected": selected.Seq}))
	_, err = io.Copy(io.Discard, os.Stdin)
	must(err)
	must(server.Close())
	if err := <-done; !errors.Is(err, http.ErrServerClosed) {
		must(err)
	}
}
