package update

import (
	"fmt"
	"os"
	"slices"
	"strings"

	"github.com/spf13/cobra"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
)

func Reconcile(lanes []Lane, tasks []ClaudeTask, v view) []string {
	owned := map[string]bool{}
	var drift []string
	for _, task := range tasks {
		if !slices.Contains(openTasks, task.Status) || task.Owner == "" {
			continue
		}
		owned[task.Owner] = true
		lane, registered := v.byLane[task.Owner]
		if !registered || lane.State != retired {
			continue
		}
		successor := v.headOf(lane)
		if successor.Name != lane.Name && successor.State != retired {
			drift = append(drift, fmt.Sprintf("task #%s %s: %s: %s retired; move it to %s", task.ID, task.Owner, clip(task.Subject), task.Owner, successor.Name))
			continue
		}
		drift = append(drift, fmt.Sprintf("task #%s %s: %s: %s retired with no successor; complete it or start one", task.ID, task.Owner, clip(task.Subject), task.Owner))
	}
	for _, lane := range working(lanes, v.now) {
		if !owned[lane.Name] {
			drift = append(drift, fmt.Sprintf("no task: %s is live with none; TaskCreate for %s", lane.Name, orWord(lane.Deliverable, "its brief")))
		}
	}
	return drift
}

func TasksCommand(a *app.App) *cobra.Command {
	var driveName string
	tasks := &cobra.Command{Use: "tasks", Short: "The coordinator's task list against the lane registry"}
	reconcile := &cobra.Command{
		Use:   "reconcile",
		Short: "Print each task and lane that disagree and exit 1 when any do",
		Args:  cobra.NoArgs,
		RunE: func(c *cobra.Command, _ []string) error {
			opened, err := open(a, driveName)
			if err != nil {
				return err
			}
			defer opened.store.Close()
			lanes, err := opened.src.Lanes(c.Context())
			if err != nil {
				return err
			}
			list, err := opened.src.Tasks()
			if err != nil {
				return err
			}
			drift := Reconcile(lanes, list, newView(lanes, nil, nil, a.Now()))
			if len(drift) == 0 {
				fmt.Fprintln(c.OutOrStdout(), "the task list matches the registry")
				return nil
			}
			fmt.Fprintln(c.OutOrStdout(), strings.Join(drift, "\n"))
			return fmt.Errorf("%d disagree", len(drift))
		},
	}
	reconcile.Flags().StringVar(&driveName, "drive", os.Getenv("LR_DRIVE"), "the drive's bus name")
	tasks.AddCommand(reconcile)
	return tasks
}
