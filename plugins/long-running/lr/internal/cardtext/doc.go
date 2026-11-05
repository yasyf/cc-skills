package cardtext

import (
	"fmt"
	"regexp"
)

var (
	docPR    = regexp.MustCompile(`(?i)\bdesign-docs(?:/pull/|#)(\d+)`)
	docPage  = regexp.MustCompile(`https://(?:docs\.poetic\.design|[\w-]+\.github\.io)/`)
	mergeAsk = regexp.MustCompile(`(?i)\b(?:merg|approv)\w*`)
)

func UnshownDoc(asked string) error {
	doc := docPR.FindStringSubmatch(asked)
	if doc == nil || !mergeAsk.MatchString(asked) || docPage.MatchString(asked) {
		return nil
	}
	return fmt.Errorf("NO DOC PAGE: this asks him to merge or approve design-docs#%s and links no rendered page of it, so he would merge a doc he has not read and nothing went out; add the doc's GitHub Pages link, https://docs.poetic.design/design-docs/<slug>/ or https://docs.poetic.design/incident-retros/<slug>/ for a retro", doc[1])
}
