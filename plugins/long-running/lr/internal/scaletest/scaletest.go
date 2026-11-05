package scaletest

import "os"

func N() int {
	if os.Getenv("LR_TEST_SCALE") == "run" {
		return 1000
	}
	return 100
}
