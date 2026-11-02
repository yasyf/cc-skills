source "$(dirname "$0")/../lib.sh"
uvx --isolated --from 'capt-hook==12.108.46' capt-hook pack test plugins/long-running
