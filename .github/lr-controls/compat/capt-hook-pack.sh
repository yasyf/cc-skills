source "$(dirname "$0")/../lib.sh"
uvx --isolated --from 'capt-hook==12.108.39' capt-hook pack test plugins/long-running
