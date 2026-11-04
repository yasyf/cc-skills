//go:build !linux

package awake

func KernelLog() ([]byte, error) {
	return nil, nil
}
