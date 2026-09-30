package main

import "os"

func invocationPath() (string, error) { return os.Executable() }
