use serde_json::json;

use super::*;

fn rules(rules: Value) -> Rules {
    Rules::parse(rules.as_array().expect("array")).expect("rules")
}

fn allowed(rules: &Rules, arguments: Value) -> bool {
    matches!(
        rules.check("alpha", "search", &arguments),
        Verdict::Allow(_)
    )
}

fn constrained(constraint: Value) -> Rules {
    rules(json!([{
        "server": "alpha", "tool": "search", "effect": "allow",
        "arguments": {"query": constraint},
    }]))
}

#[test]
fn a_constraint_needs_its_argument() {
    let rules = constrained(json!({"equals": null}));

    assert!(allowed(&rules, json!({"query": null})));
    assert!(!allowed(&rules, json!({})));
    assert!(!allowed(&rules, json!("query")));
}

#[test]
fn equality_and_prefix_compare_the_whole_value() {
    let equal = constrained(json!({"equals": {"a": [1, "b"]}}));
    let prefix = constrained(json!({"prefix": "/workspace/"}));

    assert!(allowed(&equal, json!({"query": {"a": [1, "b"]}})));
    assert!(!allowed(&equal, json!({"query": {"a": [1]}})));
    assert!(allowed(&prefix, json!({"query": "/workspace/a"})));
    assert!(!allowed(&prefix, json!({"query": "/etc/workspace/"})));
    assert!(!allowed(&prefix, json!({"query": 7})));
}

#[test]
fn a_regex_matches_the_whole_string() {
    let rules = constrained(json!({"regex": "https://example\\.com/[a-z]+|ping"}));

    assert!(allowed(
        &rules,
        json!({"query": "https://example.com/docs"})
    ));
    assert!(allowed(&rules, json!({"query": "ping"})));
    assert!(!allowed(
        &rules,
        json!({"query": "https://example.com/docs/../x"})
    ));
    assert!(!allowed(&rules, json!({"query": "xping"})));
    assert!(!allowed(&rules, json!({"query": ["ping"]})));
}

#[test]
fn a_schema_fragment_is_checked() {
    let rules = constrained(json!({"schema": {
        "type": "object",
        "properties": {
            "method": {"enum": ["GET", "HEAD"]},
            "depth": {"type": "integer", "minimum": 1, "maximum": 3},
            "tags": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 4}},
            "name": {"type": ["string", "null"], "pattern": "^a"},
            "mode": {"const": "ro"},
        },
        "required": ["method"],
        "additionalProperties": false,
    }}));
    let passes = |query: Value| allowed(&rules, json!({ "query": query }));

    assert!(passes(json!({"method": "GET"})));
    assert!(passes(json!({
        "method": "HEAD", "depth": 3, "tags": ["a", "abcd"], "name": "alpha", "mode": "ro",
    })));
    assert!(passes(json!({"method": "GET", "name": null})));
    assert!(!passes(json!({})));
    assert!(!passes(json!({"method": "POST"})));
    assert!(!passes(json!({"method": "GET", "depth": 4})));
    assert!(!passes(json!({"method": "GET", "depth": 1.5})));
    assert!(!passes(json!({"method": "GET", "tags": ["abcde"]})));
    assert!(!passes(json!({"method": "GET", "tags": [""]})));
    assert!(!passes(json!({"method": "GET", "name": "beta"})));
    assert!(!passes(json!({"method": "GET", "mode": "rw"})));
    assert!(!passes(json!({"method": "GET", "other": 1})));
    assert!(!passes(json!("GET")));
}

#[test]
fn deny_wins_and_no_rule_denies() {
    let rules = rules(json!([
        {"server": "alpha", "tool": "*", "effect": "allow"},
        {"server": "alpha", "tool": "delete", "effect": "deny"},
        {"server": "beta", "resource": "docs://beta/", "effect": "allow"},
    ]));

    assert_eq!(
        rules.check("alpha", "search", &json!({})),
        Verdict::Allow(0)
    );
    assert_eq!(
        rules.check("alpha", "delete", &json!({})),
        Verdict::Deny {
            rule: Some(1),
            reason: "denied by rule 1".into()
        }
    );
    assert_eq!(
        rules.check("beta", "search", &json!({})),
        Verdict::Deny {
            rule: None,
            reason: NO_RULE.into()
        }
    );
    assert!(rules.could_allow("alpha", "search"));
    assert!(!rules.could_allow("alpha", "delete"));
    assert!(!rules.could_allow("beta", "search"));
    assert!(rules.lists_tools("alpha") && !rules.lists_tools("beta"));
    assert!(rules.lists_resources("beta") && !rules.lists_resources("alpha"));
}

#[test]
fn budgets_fall_through_to_the_next_allow_rule() {
    let rules = rules(json!([
        {"server": "alpha", "tool": "search", "effect": "allow", "max_calls": 2},
        {"server": "alpha", "tool": "*", "effect": "allow", "max_calls_per_minute": 1},
    ]));
    let verdicts: Vec<Verdict> = (0..4)
        .map(|_| rules.check("alpha", "search", &json!({})))
        .collect();

    assert_eq!(
        verdicts[..3],
        [Verdict::Allow(0), Verdict::Allow(0), Verdict::Allow(1)]
    );
    assert_eq!(
        verdicts[3],
        Verdict::Deny {
            rule: Some(0),
            reason: "budget of rule 0 is spent".into()
        }
    );
}

#[test]
fn a_denied_call_spends_no_budget() {
    let rules = rules(json!([
        {"server": "alpha", "tool": "search", "effect": "allow", "max_calls": 1},
        {"server": "alpha", "tool": "search", "effect": "deny",
         "arguments": {"scope": {"equals": "admin"}}},
    ]));

    assert!(!allowed(&rules, json!({"scope": "admin"})));
    assert!(allowed(&rules, json!({"scope": "user"})));
}

#[test]
fn resources_follow_allow_and_deny_prefixes() {
    let rules = rules(json!([
        {"server": "alpha", "resource": "docs://", "effect": "allow"},
        {"server": "alpha", "resource": "docs://private/", "effect": "deny"},
    ]));

    assert_eq!(rules.resource("alpha", "docs://guide"), Ok(("docs://", 0)));
    assert_eq!(rules.resource("alpha", "docs://private/key"), Err(Some(1)));
    assert_eq!(rules.resource("alpha", "file://guide"), Err(None));
    assert_eq!(rules.resource("beta", "docs://guide"), Err(None));
}

#[test]
fn a_rule_the_broker_cannot_enforce_fails_the_gate() {
    for rule in [
        json!({"server": "alpha", "effect": "allow"}),
        json!({"server": "alpha", "tool": "a", "resource": "docs://", "effect": "allow"}),
        json!({"server": "alpha", "tool": "a", "effect": "audit"}),
        json!({"server": "alpha", "tool": "a", "effect": "allow", "max_calls": 0}),
        json!({"server": "alpha", "tool": "a", "effect": "allow", "extra": 1}),
        json!({"server": "alpha", "tool": "a", "effect": "allow", "arguments": {"q": {}}}),
        json!({"server": "alpha", "tool": "a", "effect": "allow",
               "arguments": {"q": {"contains": "x"}}}),
        json!({"server": "alpha", "tool": "a", "effect": "allow",
               "arguments": {"q": {"regex": "(?=a)b"}}}),
        json!({"server": "alpha", "tool": "a", "effect": "allow",
               "arguments": {"q": {"schema": {"format": "uri"}}}}),
        json!({"server": "alpha", "tool": "a", "effect": "allow",
               "arguments": {"q": {"schema": {"pattern": "(a"}}}}),
    ] {
        assert!(Rules::parse(std::slice::from_ref(&rule)).is_err(), "{rule}");
    }
}

#[test]
fn a_rule_that_stays_keeps_what_it_spent() {
    let kept = json!({"server": "alpha", "tool": "search", "effect": "allow", "max_calls": 1});
    let fresh = json!({"server": "alpha", "tool": "fetch", "effect": "allow", "max_calls": 1});
    let held = Rules::parse(std::slice::from_ref(&kept)).expect("rules");
    assert_eq!(held.check("alpha", "search", &json!({})), Verdict::Allow(0));

    let next = held.succeed(&[fresh, kept]).expect("rules");

    assert_eq!(next.check("alpha", "fetch", &json!({})), Verdict::Allow(0));
    assert_eq!(
        next.check("alpha", "search", &json!({})),
        Verdict::Deny {
            rule: Some(1),
            reason: "budget of rule 1 is spent".into()
        }
    );
}
