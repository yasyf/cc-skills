package incident

const compactRecord = `replace(replace(replace(replace(record, ' ', ''), char(9), ''), char(10), ''), char(13), '')`

const compactTarget = `replace(replace(replace(replace(json_extract(record, '$.target'), ' ', ''), char(9), ''), char(10), ''), char(13), '')`

const blockerCandidates = `
CASE WHEN kind = 'bus' AND status = 'completed' THEN
    CASE
        WHEN NOT json_valid(record) THEN 1
        WHEN json_type(record, '$.target') IS NOT 'text' THEN 1
        WHEN NOT json_valid(json_extract(record, '$.target')) THEN 1
        WHEN json_type(json_extract(record, '$.target')) IS NOT 'object' THEN 1
        WHEN instr(lower(record), '\u006') OR instr(lower(record), '\u007') THEN 1
        WHEN length(` + compactRecord + `) - length(replace(` + compactRecord + `, '"target":', '')) > 9 THEN 1
        WHEN length(` + compactTarget + `) - length(replace(` + compactTarget + `, '"kind":', '')) > 7 THEN 1
        ELSE json_extract(json_extract(record, '$.target'), '$.kind') = 'blocker'
    END
ELSE 0 END
`

const directMergeCandidates = `
CASE WHEN kind = 'bus' THEN
    CASE
        WHEN NOT json_valid(record) THEN 1
        WHEN json_type(record, '$.action_id') IS NOT 'text' THEN 1
        WHEN instr(lower(record), '\u006') OR instr(lower(record), '\u007') OR instr(lower(record), '\u005f') THEN 1
        WHEN length(` + compactRecord + `) - length(replace(` + compactRecord + `, '"action_id":', '')) > 12 THEN 1
        ELSE substr(json_extract(record, '$.action_id'), 1, 14) = 'merged-direct:'
    END
ELSE 0 END
`

const refusalPRKey = `
CASE
    WHEN NOT json_valid(record) THEN NULL
    WHEN json_type(record, '$.target') IS NOT 'text' THEN NULL
    WHEN NOT json_valid(json_extract(record, '$.target')) THEN NULL
    WHEN json_type(json_extract(record, '$.target')) IS NOT 'object' THEN NULL
    WHEN instr(lower(record), '\u006') OR instr(lower(record), '\u007') THEN NULL
    WHEN length(` + compactRecord + `) - length(replace(` + compactRecord + `, '"target":', '')) > 9 THEN NULL
    WHEN length(` + compactTarget + `) - length(replace(` + compactTarget + `, '"kind":', '')) > 7 THEN NULL
    WHEN length(` + compactTarget + `) - length(replace(` + compactTarget + `, '"pr":', '')) > 5 THEN NULL
    WHEN json_extract(json_extract(record, '$.target'), '$.kind') IS NOT 'blocker' THEN X''
    WHEN json_type(json_extract(record, '$.target'), '$.pr') = 'text' THEN
        CASE WHEN instr(json_extract(json_extract(record, '$.target'), '$.pr'), char(0)) THEN NULL
        ELSE json_extract(json_extract(record, '$.target'), '$.pr') END
    WHEN json_type(json_extract(record, '$.target'), '$.pr') = 'integer'
        AND typeof(json_extract(json_extract(record, '$.target'), '$.pr')) = 'integer'
        THEN CAST(json_extract(json_extract(record, '$.target'), '$.pr') AS TEXT)
    ELSE NULL
END
`

const releaseClockTable = `CREATE TABLE release_action_clock (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    epoch BLOB NOT NULL,
    revision INTEGER NOT NULL CHECK(typeof(revision) = 'integer' AND revision >= 1)
)`

const releaseVersionTable = `CREATE TABLE release_action_version (
    action_key TEXT PRIMARY KEY,
    ordinal INTEGER NOT NULL UNIQUE,
    revision INTEGER NOT NULL
) WITHOUT ROWID`

const releaseChangedIndex = `CREATE INDEX release_action_changed ON release_action_version(revision, ordinal)`

const releaseClockSeed = `INSERT INTO release_action_clock(singleton, epoch, revision) VALUES (1, randomblob(16), 1)`

const releaseVersionSeed = `INSERT INTO release_action_version(action_key, ordinal, revision)
SELECT action_key, ordinal, 1 FROM action WHERE kind = 'release'`

const releaseInsertTrigger = `CREATE TRIGGER release_action_insert AFTER INSERT ON action
WHEN NEW.kind = 'release' OR EXISTS (
    SELECT 1 FROM release_action_version WHERE action_key = NEW.action_key OR ordinal = NEW.ordinal
)
BEGIN
    UPDATE release_action_clock SET revision = revision + 1 WHERE singleton = 1;
    DELETE FROM release_action_version WHERE action_key = NEW.action_key OR ordinal = NEW.ordinal;
    INSERT INTO release_action_version(action_key, ordinal, revision)
    SELECT NEW.action_key, NEW.ordinal, revision FROM release_action_clock
    WHERE singleton = 1 AND NEW.kind = 'release';
END`

const releaseUpdateTrigger = `CREATE TRIGGER release_action_update AFTER UPDATE ON action
WHEN (
    OLD.kind = 'release' OR NEW.kind = 'release' OR EXISTS (
        SELECT 1 FROM release_action_version
        WHERE action_key IN (OLD.action_key, NEW.action_key) OR ordinal IN (OLD.ordinal, NEW.ordinal)
    )
) AND (
    OLD.action_key IS NOT NEW.action_key OR OLD.ordinal IS NOT NEW.ordinal OR OLD.kind IS NOT NEW.kind
    OR OLD.status IS NOT NEW.status OR OLD.record IS NOT NEW.record
)
BEGIN
    UPDATE release_action_clock SET revision = revision + 1 WHERE singleton = 1;
    DELETE FROM release_action_version
    WHERE action_key IN (OLD.action_key, NEW.action_key) OR ordinal IN (OLD.ordinal, NEW.ordinal);
    INSERT INTO release_action_version(action_key, ordinal, revision)
    SELECT NEW.action_key, NEW.ordinal, revision FROM release_action_clock
    WHERE singleton = 1 AND NEW.kind = 'release';
END`

const releaseDeleteTrigger = `CREATE TRIGGER release_action_delete AFTER DELETE ON action
WHEN OLD.kind = 'release' OR EXISTS (
    SELECT 1 FROM release_action_version WHERE action_key = OLD.action_key OR ordinal = OLD.ordinal
)
BEGIN
    UPDATE release_action_clock SET revision = revision + 1 WHERE singleton = 1;
    DELETE FROM release_action_version WHERE action_key = OLD.action_key OR ordinal = OLD.ordinal;
END`

const releaseActionDDL = releaseClockTable + ";\n" + releaseVersionTable + ";\n" + releaseChangedIndex + ";\n" +
	releaseClockSeed + ";\n" + releaseVersionSeed + ";\n" + releaseInsertTrigger + ";\n" + releaseUpdateTrigger + ";\n" +
	releaseDeleteTrigger

const escalationSchema = `
CREATE TABLE escalation_pr (
    pr TEXT NOT NULL,
    action_ordinal INTEGER NOT NULL,
    PRIMARY KEY(pr, action_ordinal)
) WITHOUT ROWID;
CREATE INDEX escalation_pr_action ON escalation_pr(action_ordinal);
CREATE TRIGGER escalation_added AFTER INSERT ON action WHEN NEW.kind = 'escalation' BEGIN
    INSERT INTO escalation_pr(pr, action_ordinal)
    SELECT DISTINCT substr(part.value, 1, length(part.value) - length(ltrim(part.value, '0123456789'))), NEW.ordinal
    FROM json_each('[' || replace(json_quote(json_extract(NEW.record, '$.target')), '#', '","') || ']') AS part
    WHERE part.key > 0 AND part.value GLOB '[0-9]*';
END;
CREATE TRIGGER escalation_changed AFTER UPDATE OF kind, record ON action
WHEN CASE
    WHEN OLD.kind != 'escalation' AND NEW.kind != 'escalation' THEN 0
    WHEN OLD.kind IS NOT NEW.kind THEN 1
    ELSE json_extract(OLD.record, '$.target') IS NOT json_extract(NEW.record, '$.target')
END BEGIN
    DELETE FROM escalation_pr WHERE action_ordinal = OLD.ordinal;
    INSERT INTO escalation_pr(pr, action_ordinal)
    SELECT DISTINCT substr(part.value, 1, length(part.value) - length(ltrim(part.value, '0123456789'))), NEW.ordinal
    FROM json_each('[' || replace(json_quote(json_extract(NEW.record, '$.target')), '#', '","') || ']') AS part
    WHERE NEW.kind = 'escalation' AND part.key > 0 AND part.value GLOB '[0-9]*';
END;
CREATE TRIGGER escalation_deleted AFTER DELETE ON action BEGIN
    DELETE FROM escalation_pr WHERE action_ordinal = OLD.ordinal;
END;
`

const schema = `
CREATE TABLE incident (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    incident_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    owner_generation INTEGER NOT NULL,
    opened_at TEXT NOT NULL,
    input_revision INTEGER NOT NULL,
    status TEXT NOT NULL,
    pending_owner TEXT,
    action_count INTEGER NOT NULL DEFAULT 0,
    fact_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE metadata (name TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE fact (
    ordinal INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    parent TEXT NOT NULL,
    key TEXT NOT NULL,
    kind TEXT NOT NULL,
    record TEXT,
    child_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX fact_parent ON fact(parent, ordinal);
CREATE INDEX fact_value ON fact(key, record, parent) WHERE kind = 'value';
CREATE TRIGGER fact_added AFTER INSERT ON fact BEGIN
    UPDATE incident SET fact_count = fact_count + 1 WHERE singleton = 1 AND NEW.parent = '';
    UPDATE fact SET child_count = child_count + 1 WHERE path = NEW.parent;
END;
CREATE TRIGGER fact_deleted AFTER DELETE ON fact BEGIN
    UPDATE incident SET fact_count = fact_count - 1 WHERE singleton = 1 AND OLD.parent = '';
    UPDATE fact SET child_count = child_count - 1 WHERE path = OLD.parent;
END;
CREATE TABLE action (
    ordinal INTEGER PRIMARY KEY AUTOINCREMENT,
    action_key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    record TEXT NOT NULL
);
CREATE INDEX action_kind ON action(kind, ordinal);
CREATE INDEX action_status ON action(status, ordinal);
CREATE INDEX action_kind_status ON action(kind, status, ordinal);
CREATE INDEX action_blocker_candidates ON action(ordinal) WHERE ` + blockerCandidates + `;
CREATE INDEX action_direct_merge_candidates ON action(ordinal) WHERE ` + directMergeCandidates + `;
CREATE INDEX action_refusal_pr ON action((coalesce((` + refusalPRKey + `), X'00')), ordinal) WHERE kind = 'bus';
CREATE TRIGGER action_added AFTER INSERT ON action BEGIN
    UPDATE incident SET action_count = action_count + 1 WHERE singleton = 1;
END;
CREATE TRIGGER action_deleted AFTER DELETE ON action BEGIN
    UPDATE incident SET action_count = action_count - 1 WHERE singleton = 1;
END;
PRAGMA user_version = 2;
` + "\n" + releaseActionDDL + ";\n" + escalationSchema
