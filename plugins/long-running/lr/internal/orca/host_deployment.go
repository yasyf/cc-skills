package orca

import (
	"context"
	"encoding/json"
	"errors"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const cciMember = "cci"

type HostTool struct {
	URL    string `json:"url"`
	SHA256 string `json:"sha256"`
	Member string `json:"member"`
}

type HostPlan struct {
	CCI        HostTool `json:"cci"`
	LR         HostTool `json:"lr"`
	Drive      string   `json:"drive"`
	RelayURL   string   `json:"relay_url"`
	RelayOwner string   `json:"relay_owner"`
}

func HostDeployment(ctx context.Context, runner shell.Runner, config string) (HostPlan, error) {
	env, err := runnerEnv(config)
	if err != nil {
		return HostPlan{}, err
	}
	pins, err := readPins(env)
	if err != nil {
		return HostPlan{}, err
	}
	plan := HostPlan{
		CCI: HostTool{pins.ccURL, pins.ccSHA, cciMember},
		LR:  HostTool{pins.lrURL, pins.lrSHA, pins.lrMember},
	}
	status, err := runner.Run(ctx, shell.Command{Argv: []string{"cci", "relay", "status", "--json"}})
	if err != nil {
		return HostPlan{}, err
	}
	var meshes []struct {
		Mesh   string   `json:"mesh"`
		URL    string   `json:"url"`
		Routes []string `json:"routes"`
	}
	if err := json.Unmarshal([]byte(status.Stdout), &meshes); err != nil {
		return HostPlan{}, err
	}
	for _, mesh := range meshes {
		if mesh.Mesh == "default" && len(mesh.Routes) > 0 {
			plan.RelayURL, plan.Drive = mesh.URL, strings.Join(mesh.Routes, ",")
		}
	}
	if plan.RelayURL == "" {
		return HostPlan{}, errors.New("cci relay status names no default mesh with routes")
	}
	node, err := runner.Run(ctx, shell.Command{Argv: []string{"cci", "node", "--json"}})
	if err != nil {
		return HostPlan{}, err
	}
	var owner struct {
		PublicKey string `json:"public_key"`
	}
	if err := json.Unmarshal([]byte(node.Stdout), &owner); err != nil || owner.PublicKey == "" {
		return HostPlan{}, errors.New("cci node names no public key")
	}
	plan.RelayOwner = owner.PublicKey
	return plan, nil
}
