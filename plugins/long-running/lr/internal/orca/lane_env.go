package orca

import "strings"

const (
	claudeLaneEnv = "DISABLE_AUTOUPDATER=1 CLAUDE_CODE_DISABLE_FILE_CHECKPOINTING=1 CHOKIDAR_USEPOLLING=1 CHOKIDAR_INTERVAL=300000 " +
		"CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1 CLAUDE_CODE_DISABLE_MOUSE=1 CLAUDE_CODE_SYNTAX_HIGHLIGHT=false " +
		"CLAUDE_CODE_DISABLE_AGENT_PROGRESS_SUMMARIES=1 CLAUDE_CODE_ENABLE_AWAY_SUMMARY=false CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION=false " +
		"CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY=1 DISABLE_COST_WARNINGS=1 DISABLE_INSTALLATION_CHECKS=1 " +
		"CLAUDE_CODE_AUTO_CONNECT_IDE=false CLAUDE_CODE_IDE_SKIP_AUTO_INSTALL=1 ENABLE_CLAUDEAI_MCP_SERVERS=false " +
		"DISABLE_TELEMETRY=1 DISABLE_ERROR_REPORTING=1 CLAUDE_CODE_GB_DISK_CACHE_WHEN_TELEMETRY_OFF=1 " +
		"CLAUDE_CODE_DISABLE_GIT_INSTRUCTIONS=1 CLAUDE_CODE_DISABLE_BUNDLED_SKILLS=1 CLAUDE_CODE_DISABLE_CRON=1"
	claudeLaneSettings = `"spinnerTipsEnabled":false,"prefersReducedMotion":true,"verbose":false,"showThinkingSummaries":false,` +
		`"feedbackDrafts":"off","awaySummaryEnabled":false,"promptSuggestionEnabled":false,"tui":"fullscreen",` +
		`"disableDeepLinkRegistration":"disable","statusLine":{"type":"command","command":"exit 0"}`
	laneDeniedTools = "AskUserQuestion,EnterPlanMode,ExitPlanMode,PushNotification,DesignSync"
	laneSettingsEnv = "CLAUDE_LANE_SETTINGS"
)

func laneSettingsFlag(env func(string) string, fragments ...string) string {
	all := append([]string{claudeLaneSettings}, fragments...)
	if local := strings.TrimSpace(env(laneSettingsEnv)); local != "" {
		all = append(all, strings.TrimSuffix(strings.TrimPrefix(local, "{"), "}"))
	}
	return " --settings " + shellQuote("{"+strings.Join(all, ",")+"}")
}
