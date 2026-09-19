//! Live Jev oracle: the TypeSafe System One API behind the `Oracle` trait.
//!
//! Measured characteristics that shape this client (n=1,406 captured calls):
//!
//! - **Not deterministic.** Identical requests returned 0.03, 0.03, 0.03,
//!   0.04, 0.04. This is the entire reason the protocol needs a stability
//!   gate; the client must not paper over it with caching.
//! - **Billing is `input_tokens` only**, with a ~281-token fixed overhead per
//!   call and state billed once regardless of question count. Batching many
//!   questions over one state is ~89% cheaper, which is why `consult_batch`
//!   exists.
//! - **Latency is flat in question count**: ~99 ms upstream for 1 question and
//!   for 38. Fan-out is close to free.
//! - Mid-range answers are NOT coin flips: items stated 0.2–0.8 were correct
//!   20/20. Persistent instability is a signal to escalate, not to guess.
//!
//! The API key is read from `TYPESAFE_API_KEY` and never logged.

use std::time::Duration;

use serde::{Deserialize, Serialize};

use crate::oracle::{Calibration, Judgment, Oracle, OracleError};

const DEFAULT_BASE_URL: &str = "https://api.typesafe.ai";
const DEFAULT_MODEL: &str = "jev-latest";

#[derive(Serialize)]
struct NoulQuestion<'a> {
    #[serde(rename = "type")]
    kind: &'a str,
    instructions: &'a str,
    #[serde(skip_serializing_if = "Option::is_none")]
    criteria: Option<NoulCriteria<'a>>,
}

#[derive(Serialize)]
struct NoulCriteria<'a> {
    #[serde(rename = "true")]
    yes: &'a str,
    #[serde(rename = "false")]
    no: &'a str,
}

#[derive(Serialize)]
struct SystemOneRequest<'a> {
    state: &'a str,
    model: &'a str,
    questions: std::collections::BTreeMap<String, NoulQuestion<'a>>,
}

#[derive(Deserialize)]
struct NoulAnswer {
    noul: f64,
}

#[derive(Deserialize)]
struct Usage {
    input_tokens: i64,
    #[allow(dead_code)]
    output_tokens: i64,
}

#[derive(Deserialize)]
struct SystemOneResponse {
    model: String,
    answers: std::collections::BTreeMap<String, NoulAnswer>,
    usage: Usage,
}

pub struct JevOracle {
    api_key: String,
    base_url: String,
    model: String,
    calibration: Calibration,
    timeout: Duration,
    /// Criteria attached to every question, so `true`/`false` mean the same
    /// thing to every agent in a round. Misaligned criteria measurably degrade
    /// answers.
    criteria: Option<(String, String)>,
}

impl JevOracle {
    /// Build from the `TYPESAFE_API_KEY` environment variable.
    pub fn from_env(calibration: Calibration) -> Result<Self, OracleError> {
        let api_key = std::env::var("TYPESAFE_API_KEY").map_err(|_| {
            OracleError::Protocol("TYPESAFE_API_KEY is not set".into())
        })?;
        Ok(Self {
            api_key,
            base_url: std::env::var("TYPESAFE_BASE_URL")
                .unwrap_or_else(|_| DEFAULT_BASE_URL.to_string()),
            model: DEFAULT_MODEL.to_string(),
            calibration,
            timeout: Duration::from_secs(30),
            criteria: None,
        })
    }

    pub fn with_criteria(mut self, yes: impl Into<String>, no: impl Into<String>) -> Self {
        self.criteria = Some((yes.into(), no.into()));
        self
    }

    pub fn with_model(mut self, model: impl Into<String>) -> Self {
        self.model = model.into();
        self
    }

    /// Ask several independent questions about one state in a single call.
    ///
    /// Batching is the right default: state is billed once, questions cost
    /// ~19 tokens each against a ~277-token floor for a separate call, and
    /// server-side latency does not grow with question count.
    pub fn consult_batch(
        &self,
        questions: &[(String, String)],
        state: &str,
    ) -> Result<(Vec<Judgment>, i64), OracleError> {
        if questions.is_empty() {
            return Ok((Vec::new(), 0));
        }
        let criteria = self.criteria.as_ref().map(|(y, n)| NoulCriteria {
            yes: y.as_str(),
            no: n.as_str(),
        });

        let mut map = std::collections::BTreeMap::new();
        for (id, instructions) in questions {
            map.insert(
                id.clone(),
                NoulQuestion {
                    kind: "noul",
                    instructions: instructions.as_str(),
                    criteria: criteria.as_ref().map(|c| NoulCriteria {
                        yes: c.yes,
                        no: c.no,
                    }),
                },
            );
        }

        let body = SystemOneRequest {
            state,
            model: &self.model,
            questions: map,
        };

        let url = format!("{}/v1/systemone", self.base_url);
        let resp = ureq::post(&url)
            .timeout(self.timeout)
            .set("Authorization", &format!("Bearer {}", self.api_key))
            .set("Content-Type", "application/json")
            .send_json(serde_json::to_value(&body).map_err(|e| {
                OracleError::Protocol(format!("serialize request: {e}"))
            })?);

        let resp = match resp {
            Ok(r) => r,
            Err(ureq::Error::Status(429, _)) => return Err(OracleError::RateLimited),
            Err(ureq::Error::Status(code, r)) => {
                let detail = r.into_string().unwrap_or_default();
                return Err(OracleError::Protocol(format!("HTTP {code}: {detail}")));
            }
            Err(e) => return Err(OracleError::Transport(e.to_string())),
        };

        // Retain the provider's request id so any vote can be reconciled
        // against TypeSafe's own records. There is no usage API, so this
        // header is the only server-side handle we get.
        let request_id = resp.header("x-typesafe-request-id").map(|s| s.to_string());

        let parsed: SystemOneResponse = resp
            .into_json()
            .map_err(|e| OracleError::Protocol(format!("decode response: {e}")))?;
        let _ = &parsed.model;

        let mut out = Vec::with_capacity(questions.len());
        for (id, _) in questions {
            let answer = parsed
                .answers
                .get(id)
                .ok_or_else(|| OracleError::Protocol(format!("missing answer for {id}")))?;
            let (verdict, evidence) = self.calibration.classify(answer.noul);
            out.push(Judgment {
                verdict,
                evidence,
                request_id: request_id.clone(),
            });
        }
        Ok((out, parsed.usage.input_tokens))
    }
}

impl Oracle for JevOracle {
    fn consult(
        &self,
        agent_id: &str,
        question: &str,
        state: &str,
        _attempt: i32,
    ) -> Result<Judgment, OracleError> {
        // One question per call here. A production coordinator should batch
        // all agents' questions for a round into one request; see
        // `consult_batch`.
        let qs = vec![(format!("q_{agent_id}"), question.to_string())];
        let (mut judgments, _tokens) = self.consult_batch(&qs, state)?;
        judgments
            .pop()
            .ok_or_else(|| OracleError::Protocol("no judgment returned".into()))
    }
}
