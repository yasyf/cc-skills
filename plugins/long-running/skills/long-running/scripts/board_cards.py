#!/usr/bin/env python3
import os, sys
lr = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "..", "bin", "lr")
os.execv(lr, [lr, "board", "cards", *sys.argv[1:]])
