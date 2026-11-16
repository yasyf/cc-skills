package main

import (
	"bufio"
	"encoding/json"
	"os"
)

type mcpRequest struct {
	ID     json.RawMessage `json:"id"`
	Method string          `json:"method"`
	Params struct {
		ProtocolVersion string `json:"protocolVersion"`
	} `json:"params"`
}

func idleMCPServer() {
	in := bufio.NewScanner(os.Stdin)
	in.Buffer(make([]byte, 1<<20), 1<<24)
	out := json.NewEncoder(os.Stdout)
	for in.Scan() {
		var req mcpRequest
		if json.Unmarshal(in.Bytes(), &req) != nil || len(req.ID) == 0 {
			continue
		}
		reply := map[string]any{"jsonrpc": "2.0", "id": req.ID}
		switch req.Method {
		case "initialize":
			version := req.Params.ProtocolVersion
			if version == "" {
				version = "2025-06-18"
			}
			reply["result"] = map[string]any{"protocolVersion": version, "capabilities": map[string]any{}, "serverInfo": map[string]string{"name": "codex-ask", "version": appVersion}}
		case "ping":
			reply["result"] = map[string]any{}
		case "tools/list":
			reply["result"] = map[string]any{"tools": []any{}}
		default:
			reply["error"] = map[string]any{"code": -32601, "message": "codex-ask serves no " + req.Method}
		}
		if out.Encode(reply) != nil {
			return
		}
	}
}
