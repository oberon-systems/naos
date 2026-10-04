//! The rules of a Run's mcp policy: what a call must match before it reaches a gate or a server.
use std::collections::BTreeMap;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use regex::Regex;
use serde::Deserialize;
use serde_json::Value;

use crate::libs::error::AgentError;

const WINDOW: Duration = Duration::from_secs(60);
const ANY_TOOL: &str = "*";
const MAX_DEPTH: usize = 8;
pub const NO_RULE: &str = "no rule allows this call";

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct RuleDoc {
    server: String,
    tool: Option<String>,
    resource: Option<String>,
    effect: Effect,
    #[serde(default)]
    arguments: BTreeMap<String, Value>,
    #[serde(default)]
    max_calls_per_minute: Option<u32>,
    #[serde(default)]
    max_calls: Option<u64>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "lowercase")]
enum Effect {
    Allow,
    Deny,
}

#[derive(Debug)]
enum Target {
    Tool(String),
    Resource(String),
}

#[derive(Debug)]
enum Constraint {
    Equals(Value),
    Prefix(String),
    Regex(Regex),
    Schema(Box<Schema>),
}

impl Constraint {
    fn parse(value: &Value) -> Result<Self, String> {
        let fields = value.as_object().filter(|fields| fields.len() == 1);
        let (kind, value) = fields
            .and_then(|fields| fields.iter().next())
            .ok_or("a constraint sets exactly one kind")?;
        match (kind.as_str(), value) {
            ("equals", value) => Ok(Self::Equals(value.clone())),
            ("prefix", Value::String(prefix)) => Ok(Self::Prefix(prefix.clone())),
            // The whole value must match, so an allow rule cannot be passed by a longer string.
            ("regex", Value::String(pattern)) => {
                compile(&format!("^(?:{pattern})$")).map(Self::Regex)
            }
            ("schema", schema) => Ok(Self::Schema(Box::new(Schema::parse(schema, 0)?))),
            _ => Err(format!("constraint {kind:?} is malformed")),
        }
    }

    fn matches(&self, value: &Value) -> bool {
        match self {
            Self::Equals(wanted) => wanted == value,
            Self::Prefix(prefix) => value.as_str().is_some_and(|text| text.starts_with(prefix)),
            Self::Regex(regex) => value.as_str().is_some_and(|text| regex.is_match(text)),
            Self::Schema(schema) => schema.matches(value),
        }
    }
}

fn compile(pattern: &str) -> Result<Regex, String> {
    Regex::new(pattern).map_err(|_| "a pattern does not compile".to_owned())
}

#[derive(Debug)]
enum Additional {
    Any(bool),
    Schema(Box<Schema>),
}

/// The subset of JSON Schema the API accepts; anything else fails the gate.
#[derive(Debug, Default)]
struct Schema {
    types: Option<Vec<String>>,
    choices: Option<Vec<Value>>,
    constant: Option<Value>,
    minimum: Option<f64>,
    maximum: Option<f64>,
    min_length: Option<u64>,
    max_length: Option<u64>,
    pattern: Option<Regex>,
    items: Option<Box<Schema>>,
    properties: BTreeMap<String, Schema>,
    required: Vec<String>,
    additional: Option<Additional>,
}

impl Schema {
    fn parse(node: &Value, depth: usize) -> Result<Self, String> {
        let fields = node
            .as_object()
            .filter(|_| depth <= MAX_DEPTH)
            .ok_or("a schema is not an object or nests too deep")?;
        let mut schema = Self::default();
        for (key, value) in fields {
            let malformed = || format!("schema keyword {key:?} is malformed");
            match key.as_str() {
                "type" => {
                    let types = match value {
                        Value::String(name) => vec![name.clone()],
                        other => serde_json::from_value(other.clone()).map_err(|_| malformed())?,
                    };
                    schema.types = Some(types);
                }
                "enum" => schema.choices = Some(value.as_array().ok_or_else(malformed)?.clone()),
                "const" => schema.constant = Some(value.clone()),
                "minimum" => schema.minimum = Some(value.as_f64().ok_or_else(malformed)?),
                "maximum" => schema.maximum = Some(value.as_f64().ok_or_else(malformed)?),
                "minLength" => schema.min_length = Some(value.as_u64().ok_or_else(malformed)?),
                "maxLength" => schema.max_length = Some(value.as_u64().ok_or_else(malformed)?),
                "pattern" => {
                    schema.pattern = Some(compile(value.as_str().ok_or_else(malformed)?)?);
                }
                "items" => schema.items = Some(Box::new(Self::parse(value, depth + 1)?)),
                "properties" => {
                    for (name, child) in value.as_object().ok_or_else(malformed)? {
                        let child = Self::parse(child, depth + 1)?;
                        schema.properties.insert(name.clone(), child);
                    }
                }
                "required" => {
                    schema.required =
                        serde_json::from_value(value.clone()).map_err(|_| malformed())?;
                }
                "additionalProperties" => {
                    schema.additional = Some(match value {
                        Value::Bool(any) => Additional::Any(*any),
                        other => Additional::Schema(Box::new(Self::parse(other, depth + 1)?)),
                    });
                }
                _ => return Err(format!("schema keyword {key:?} is not supported")),
            }
        }
        Ok(schema)
    }

    fn matches(&self, value: &Value) -> bool {
        let typed = self
            .types
            .as_ref()
            .is_none_or(|types| types.iter().any(|name| is_type(name, value)));
        let chosen = self
            .choices
            .as_ref()
            .is_none_or(|choices| choices.contains(value));
        let constant = self.constant.as_ref().is_none_or(|wanted| wanted == value);
        typed
            && chosen
            && constant
            && self.number(value)
            && self.text(value)
            && self.array(value)
            && self.object(value)
    }

    fn number(&self, value: &Value) -> bool {
        let Some(number) = value.as_f64() else {
            return true;
        };
        self.minimum.is_none_or(|minimum| number >= minimum)
            && self.maximum.is_none_or(|maximum| number <= maximum)
    }

    fn text(&self, value: &Value) -> bool {
        let Some(text) = value.as_str() else {
            return true;
        };
        let length = text.chars().count() as u64;
        self.min_length.is_none_or(|minimum| length >= minimum)
            && self.max_length.is_none_or(|maximum| length <= maximum)
            && self
                .pattern
                .as_ref()
                .is_none_or(|regex| regex.is_match(text))
    }

    fn array(&self, value: &Value) -> bool {
        match (value.as_array(), &self.items) {
            (Some(items), Some(schema)) => items.iter().all(|item| schema.matches(item)),
            _ => true,
        }
    }

    fn object(&self, value: &Value) -> bool {
        let Some(fields) = value.as_object() else {
            return true;
        };
        self.required.iter().all(|name| fields.contains_key(name))
            && fields.iter().all(|(name, field)| {
                match (self.properties.get(name), &self.additional) {
                    (Some(schema), _) => schema.matches(field),
                    (None, Some(Additional::Any(any))) => *any,
                    (None, Some(Additional::Schema(schema))) => schema.matches(field),
                    (None, None) => true,
                }
            })
    }
}

fn is_type(name: &str, value: &Value) -> bool {
    match name {
        "string" => value.is_string(),
        "number" => value.is_number(),
        "integer" => value.as_f64().is_some_and(|number| number.fract() == 0.0),
        "boolean" => value.is_boolean(),
        "object" => value.is_object(),
        "array" => value.is_array(),
        _ => name == "null" && value.is_null(),
    }
}

#[derive(Debug)]
struct Spent {
    window: Instant,
    in_window: u32,
    total: u64,
}

#[derive(Debug)]
struct Rule {
    server: String,
    target: Target,
    effect: Effect,
    arguments: Vec<(String, Constraint)>,
    per_minute: Option<u32>,
    per_run: Option<u64>,
    spent: Mutex<Spent>,
}

impl Rule {
    fn parse(value: &Value) -> Result<Self, String> {
        let doc: RuleDoc = serde_json::from_value(value.clone()).map_err(|err| err.to_string())?;
        let target = match (doc.tool, doc.resource) {
            (Some(tool), None) => Target::Tool(tool),
            (None, Some(prefix)) if doc.arguments.is_empty() => Target::Resource(prefix),
            _ => return Err("a rule names either a tool or a resource".into()),
        };
        let arguments = doc
            .arguments
            .iter()
            .map(|(name, constraint)| Ok((name.clone(), Constraint::parse(constraint)?)))
            .collect::<Result<_, String>>()?;
        if doc.max_calls_per_minute == Some(0) || doc.max_calls == Some(0) {
            return Err("a budget of zero calls".into());
        }
        Ok(Self {
            server: doc.server,
            target,
            effect: doc.effect,
            arguments,
            per_minute: doc.max_calls_per_minute,
            per_run: doc.max_calls,
            spent: Mutex::new(Spent {
                window: Instant::now(),
                in_window: 0,
                total: 0,
            }),
        })
    }

    fn names(&self, server: &str, tool: &str) -> bool {
        matches!(&self.target, Target::Tool(named) if named == tool || named == ANY_TOOL)
            && self.server == server
    }

    fn matches(&self, server: &str, tool: &str, arguments: &Value) -> bool {
        self.names(server, tool)
            && self.arguments.iter().all(|(name, constraint)| {
                arguments
                    .get(name)
                    .is_some_and(|value| constraint.matches(value))
            })
    }

    fn prefix(&self, server: &str, uri: &str) -> Option<&str> {
        match &self.target {
            Target::Resource(prefix) if self.server == server && uri.starts_with(prefix) => {
                Some(prefix)
            }
            _ => None,
        }
    }

    fn spend(&self) -> bool {
        let mut spent = self.spent.lock().expect("spent");
        let now = Instant::now();
        if now.duration_since(spent.window) >= WINDOW {
            spent.window = now;
            spent.in_window = 0;
        }
        let left = self.per_minute.is_none_or(|limit| spent.in_window < limit)
            && self.per_run.is_none_or(|limit| spent.total < limit);
        if left {
            spent.in_window += 1;
            spent.total += 1;
        }
        left
    }
}

/// What the rules say about one call; the index is the rule's place in the policy document.
#[derive(Debug, PartialEq, Eq)]
pub enum Verdict {
    Allow(usize),
    Deny { rule: Option<usize>, reason: String },
}

#[derive(Debug, Default)]
pub struct Rules {
    rules: Vec<Rule>,
}

impl Rules {
    pub fn parse(rules: &[Value]) -> Result<Self, AgentError> {
        let rules = rules
            .iter()
            .enumerate()
            .map(|(index, rule)| {
                Rule::parse(rule)
                    .map_err(|reason| AgentError::Runtime(format!("mcp rule {index}: {reason}")))
            })
            .collect::<Result<_, _>>()?;
        Ok(Self { rules })
    }

    /// A matching deny wins; otherwise the first matching allow rule with budget left is charged.
    pub fn check(&self, server: &str, tool: &str, arguments: &Value) -> Verdict {
        let matching = || {
            self.rules
                .iter()
                .enumerate()
                .filter(|(_, rule)| rule.matches(server, tool, arguments))
        };
        if let Some((index, _)) = matching().find(|(_, rule)| rule.effect == Effect::Deny) {
            return Verdict::Deny {
                rule: Some(index),
                reason: format!("denied by rule {index}"),
            };
        }
        let mut spent = None;
        for (index, rule) in matching() {
            if rule.spend() {
                return Verdict::Allow(index);
            }
            spent.get_or_insert(index);
        }
        Verdict::Deny {
            rule: spent,
            reason: spent.map_or_else(
                || NO_RULE.to_owned(),
                |index| format!("budget of rule {index} is spent"),
            ),
        }
    }

    /// Whether some call of this tool could pass: what `tools/list` shows.
    pub fn could_allow(&self, server: &str, tool: &str) -> bool {
        let named = || self.rules.iter().filter(|rule| rule.names(server, tool));
        named().any(|rule| rule.effect == Effect::Allow)
            && !named().any(|rule| rule.effect == Effect::Deny && rule.arguments.is_empty())
    }

    pub fn lists_tools(&self, server: &str) -> bool {
        self.allows(server, |target| matches!(target, Target::Tool(_)))
    }

    pub fn lists_resources(&self, server: &str) -> bool {
        self.allows(server, |target| matches!(target, Target::Resource(_)))
    }

    fn allows(&self, server: &str, kind: impl Fn(&Target) -> bool) -> bool {
        self.rules
            .iter()
            .any(|rule| rule.server == server && rule.effect == Effect::Allow && kind(&rule.target))
    }

    /// The allow prefix a URI matches on this server and its rule, unless a deny prefix matches too.
    pub fn resource(&self, server: &str, uri: &str) -> Result<(&str, usize), Option<usize>> {
        let of = |effect| {
            self.rules
                .iter()
                .enumerate()
                .find_map(move |(index, rule)| {
                    let prefix = rule.prefix(server, uri).filter(|_| rule.effect == effect)?;
                    Some((prefix, index))
                })
        };
        match (of(Effect::Deny), of(Effect::Allow)) {
            (Some((_, index)), _) => Err(Some(index)),
            (None, Some(found)) => Ok(found),
            (None, None) => Err(None),
        }
    }
}

#[cfg(test)]
mod tests;
