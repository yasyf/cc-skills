package main

import (
	"os"

	"llmclient"

	"ci/internal/ai"
)

func gateway() (string, string) {
	return os.Getenv(llmclient.EnvURL), ai.URL()
}
