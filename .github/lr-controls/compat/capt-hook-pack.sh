source "$(dirname "$0")/../lib.sh"
uvx --isolated --from 'capt-hook==12.119.0' capt-hook pack test plugins/long-running
