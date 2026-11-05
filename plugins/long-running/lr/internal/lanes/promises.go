package lanes

import (
	"fmt"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const promiseSubject = "card-promise"

type Promise struct {
	Card, Lane, Question string
	By                   time.Time
}

func promisedBy(d Deps, name string) ([]Promise, error) {
	promised, err := d.Promises()
	return slices.DeleteFunc(promised, func(p Promise) bool { return p.Lane != name }), err
}

func promiseCards(promised []Promise) string {
	cards := make([]string, len(promised))
	for i, p := range promised {
		cards[i] = p.Card
	}
	return strings.Join(cards, ", ")
}

func missedPromises(s *store.Store, d Deps, now time.Time) {
	promised, err := d.Promises()
	if err != nil {
		if must(s.Escalate(promiseSubject, err.Error(), Root, now)) {
			do(d.Post(Post{Kind: "blocker", To: []string{Root}, Text: fmt.Sprintf("The card-promise check did not run: %v", err)}))
		}
		return
	}
	for _, p := range promised {
		if p.By.After(now) {
			continue
		}
		to := routeOf(s, p.Lane)
		if must(s.Escalate(promiseSubject, p.Card, to, now)) {
			do(d.Post(Post{Kind: "blocker", To: []string{to, OwnerRelay}, LaneRef: p.Lane, Attrs: map[string]string{"card": p.Card, "by": p.By.Format(time.RFC3339)}, Text: fmt.Sprintf("%s's card %s promised him something by a clock that passed with nothing delivered, and he waits on it to answer %q. %s: deliver it, then post `cci post --kind report --attr delivered=card:%s`; %s: tell him what it waits on and when it comes.", p.Lane, p.Card, p.Question, to, p.Card, OwnerRelay)}))
		}
	}
}
