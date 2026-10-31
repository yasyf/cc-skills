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
	"github.com/yasyf/cc-inbox/internal/relay"
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
	history := make([]store.Ingestion, population)
	for i := range history {
		history[i] = store.Ingestion{
			Record: store.Record{Drive: "d1", Lane: "worker", Kind: kinds.Note, At: time.Now().UTC(), Text: fmt.Sprintf("unrelated history %d", i), Source: "post"},
			LineHash: fmt.Sprintf("landing-native-history-%d", i),
		}
	}
	inserted, err := st.IngestAll(ctx, history)
	must(err)
	if inserted != population {
		panic(fmt.Errorf("seeded %d historical records, want %d", inserted, population))
	}
	st.SetOwner(false)
	must(relay.SaveConfig(home, relay.Config{Mesh: "native", URL: "https://relay.invalid", Tier: "service"}))
	db, err := sql.Open("sqlite", filepath.Join(home, store.DBFile))
	must(err)
	_, err = db.ExecContext(ctx, `INSERT INTO members(node,public_key,tier,via,"by",at) VALUES('aaaaaaaaaaaaaaaa',X'','owner','relay','fixture',1)`)
	must(err)
	must(db.Close())
	_, err = st.Apply(ctx, []store.Row{{Origin: "aaaaaaaaaaaaaaaa", OriginSeq: 2, ID: "fixture-hold-29020", Drive: "d1", Lane: "root", Kind: kinds.Hold, At: time.Now().UTC(), Text: "hold #29020", Refs: store.Refs{PRs: []store.PR{{Number: 29020}}}, Source: "post"}}, nil)
	must(err)
	holds, err := st.Query(ctx, store.Filter{Drive: "d1", Kinds: []kinds.Kind{kinds.Hold}})
	must(err)
	if len(holds) != 1 {
		panic(fmt.Errorf("seeded %d holds, want 1", len(holds)))
	}
	hold := holds[0]
	_, err = st.Apply(ctx, []store.Row{{Origin: "aaaaaaaaaaaaaaaa", OriginSeq: 7, Drive: "mixed", Lane: "root", Kind: kinds.Note, At: time.Now().UTC(), Text: "owner record", Source: "post"}}, nil)
	must(err)
	_, _, err = st.Post(ctx, store.Record{Drive: "mixed", Lane: "root", Kind: kinds.Note, Text: "local record"}, 0)
	must(err)
	_, err = st.Apply(ctx, []store.Row{{Origin: "bbbbbbbbbbbbbbbb", OriginSeq: 100, Drive: "mixed", Lane: "root", Kind: kinds.Note, At: time.Now().UTC(), Text: "remote record", Source: "post"}}, nil)
	must(err)
	mixed, err := st.Query(ctx, store.Filter{Drive: "mixed"})
	must(err)
	tail, err := cli.TailHandler(st, home)
	must(err)
	var requests atomic.Int64
	mux := http.NewServeMux()
	mux.Handle("/v1/tail", http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests.Add(1)
		tail.ServeHTTP(w, r)
	}))
	mux.HandleFunc("GET /fixture/counts", func(w http.ResponseWriter, r *http.Request) {
		respond(w, map[string]any{"native_requests": requests.Load()})
	})
	mux.HandleFunc("POST /fixture/append", func(w http.ResponseWriter, r *http.Request) {
		posted, _, err := st.Post(r.Context(), store.Record{ID: "fixture-hold-29021", Drive: "d1", Lane: "root", Kind: kinds.Hold, Text: "hold #29021", Refs: store.Refs{PRs: []store.PR{{Number: 29021}}}}, 0)
		must(err)
		respond(w, map[string]any{"seq": posted.Seq})
	})
	mux.HandleFunc("POST /fixture/resolve", func(w http.ResponseWriter, r *http.Request) {
		posted, _, err := st.Post(r.Context(), store.Record{ID: "fixture-resolve-29020", Drive: "d1", Lane: "root", Kind: kinds.Done, Text: "resolved", Resolves: hold.Seq}, 0)
		must(err)
		respond(w, map[string]any{"seq": posted.Seq})
	})
	mux.HandleFunc("POST /fixture/expire", func(w http.ResponseWriter, r *http.Request) {
		db, err := sql.Open("sqlite", filepath.Join(home, store.DBFile))
		must(err)
		defer func() { must(db.Close()) }()
		_, err = db.ExecContext(r.Context(), "UPDATE records SET expires_at=1 WHERE drive='d1' AND kind='hold'")
		must(err)
		respond(w, map[string]any{"expired": true})
	})
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	must(err)
	server := &http.Server{Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	done := make(chan error, 1)
	go func() { done <- server.Serve(listener) }()
	must(json.NewEncoder(os.Stdout).Encode(map[string]any{"url": "http://" + listener.Addr().String(), "home": home, "hold": hold.Seq, "population": inserted, "hold_number": hold.Num, "mixed": mixed}))
	_, err = io.Copy(io.Discard, os.Stdin)
	must(err)
	must(server.Close())
	if err := <-done; !errors.Is(err, http.ErrServerClosed) {
		must(err)
	}
}
