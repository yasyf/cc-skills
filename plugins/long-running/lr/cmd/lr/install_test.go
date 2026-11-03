package main

import (
	"io"
	"os"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/install"
)

func TestEveryShimRunsAVerbThisBinaryHas(t *testing.T) {
	script, err := os.ReadFile("../../../scripts/bin-shim.sh")
	if err != nil {
		t.Fatal(err)
	}
	if faults := install.ShimFaults(root(app.New(io.Discard)), string(script)); len(faults) != 0 {
		t.Fatal(faults)
	}
}
