package codexguard

import (
	"context"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
)

const keyWrite = "sprite exec -s desk -- cat service.pem | AWS_PROFILE=core-auto aws ssm put-parameter --name /ci/desk-host/cci-service-key --type SecureString --overwrite --value file:///dev/stdin"

func granting(grants string) *shelltest.Fake {
	runner := &shelltest.Fake{}
	runner.Reply(grants, grantsRead...)
	return runner
}

func TestCredentialWrites(t *testing.T) {
	granted := "lane-a=ssm:/ci/desk-host/cci-service-key\nlane-a=keychain:cci service\nlane-b=secretsmanager:prod/db\n"
	cases := []struct {
		command string
		grants  string
		refused bool
	}{
		{keyWrite, "", true},
		{"aws --profile core-auto ssm put-parameter --name /ci/desk-host/cci-service-key --overwrite --value v", "", true},
		{"cc-sudo run -- aws ssm put-parameter --name /ci/x --type SecureString --value v", "", true},
		{"cc-sudo run -- /bin/launchctl asuser 501 /usr/bin/sudo -H -u owner /usr/bin/security add-generic-password -s 'cci service' -a lane -w", "", true},
		{"env AWS_PROFILE=core-auto aws ssm delete-parameter --name /ci/x", "", true},
		{"sudo -u owner aws secretsmanager put-secret-value --secret-id prod/db --secret-string v", "", true},
		{"aws secretsmanager rotate-secret --secret-id prod/db", "", true},
		{"bash -lc 'aws secretsmanager create-secret --name prod/db --secret-string v'", "", true},
		{"x=$(aws ssm put-parameter --name=/ci/x --type SecureString --value v)", "", true},
		{"security delete-generic-password -s cci-service", "", true},
		{"aws ssm put-parameter --name /ci/x --type SecureString --value v", granted, true},
		{`aws ssm put-parameter --name "$KEY" --type SecureString --value v`, granted, true},
		{"aws ssm delete-parameters --names /ci/desk-host/cci-service-key /ci/x", granted, true},
		{"aws secretsmanager put-secret-value --secret-id prod/db --secret-string v", granted, true},
		{`KIND=Secure; aws ssm put-parameter --name /ci/x --type "${KIND}String" --value v`, "", true},
		{"aws ssm --region us-east-1 delete-parameter --name /ci/x", "", true},
		{"bash -l -c 'aws ssm delete-parameter --name /ci/x'", "", true},
		{"security add-generic-password -a lane -s='cci service' -w v", granted, true},
		{"security add-internet-password -a lane -s 'cci service' -P 443 -s other -w v", granted, true},
		{keyWrite, granted, false},
		{"security add-generic-password -a lane -s 'cci service' -w '-s=other' -U", granted, false},
		{`aws --profile "$CI_AWS_PROFILE" ssm put-parameter --name /ci/desk-host/cci-service-key --type SecureString --value v`, granted, false},
		{"security add-generic-password -a lane -s 'cci service' -w", granted, false},
		{"aws ssm delete-parameters --names /ci/desk-host/cci-service-key", granted, false},
		{"aws ssm get-parameter --name /ci/desk-host/cci-service-key --with-decryption", "", false},
		{"aws ssm put-parameter --name /config/region --type String --value us-west-2 --overwrite", "", false},
		{"aws ssm describe-parameters --parameter-filters Key=Name,Values=/ci/desk-host/cci-service-key", "", false},
		{"security find-generic-password -s cci-service", "", false},
		{"echo aws ssm put-parameter --name /ci/x --type SecureString", "", false},
	}
	for _, c := range cases {
		t.Run(c.command, func(t *testing.T) {
			got := Guard{Runner: granting(c.grants), Getenv: lane}.Refusal(context.Background(), payload(t, c.command))
			if (got == storeWriteRefusal) != c.refused {
				t.Fatalf("Refusal(%q) = %q, want refused %v", c.command, got, c.refused)
			}
		})
	}
}

func TestACredentialGrantBelongsToItsLane(t *testing.T) {
	laneB := func(key string) string {
		if key == "CLAUDE_LONG_RUNNING_LANE" {
			return "lane-b"
		}
		return ""
	}
	if (Guard{Runner: granting("lane-a=ssm:/ci/desk-host/cci-service-key\n"), Getenv: laneB}).Refusal(context.Background(), payload(t, keyWrite)) != storeWriteRefusal {
		t.Fatal("allowed a write root granted another lane")
	}
}

func TestAFailedGrantReadRefusesTheWrite(t *testing.T) {
	unset := &shelltest.Fake{}
	unset.Fail(1, "", grantsRead...)
	if (Guard{Runner: unset, Getenv: lane}).Refusal(context.Background(), payload(t, keyWrite)) != storeWriteRefusal {
		t.Fatal("allowed a write with no grant read")
	}
}

func TestReadsNoGrantsForACallThatWritesNoCredential(t *testing.T) {
	runner := granting("")
	Guard{Runner: runner, Getenv: lane}.Refusal(context.Background(), payload(t, "aws ssm get-parameter --name /ci/x"))
	if calls := runner.Calls(); len(calls) != 0 {
		t.Fatalf("read on a call that writes no credential: %v", calls)
	}
}
