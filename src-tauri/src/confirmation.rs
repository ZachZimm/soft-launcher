use crate::exec::ExecArgs;
use std::{
    collections::HashMap,
    time::{Duration, Instant},
};
pub const TTL: Duration = Duration::from_secs(15);
struct Token {
    generation: u64,
    action: String,
    args: ExecArgs,
    expires: Instant,
    plan_hash: String,
}
#[derive(Default)]
pub struct Confirmations {
    tokens: HashMap<String, Token>,
}
impl Confirmations {
    pub fn issue(
        &mut self,
        generation: u64,
        action: &str,
        args: &ExecArgs,
        now: Instant,
    ) -> Result<String, String> {
        self.issue_plan(generation, action, args, "", now)
    }
    pub fn issue_plan(
        &mut self,
        generation: u64,
        action: &str,
        args: &ExecArgs,
        plan_hash: &str,
        now: Instant,
    ) -> Result<String, String> {
        self.tokens.retain(|_, token| token.expires > now);
        if self.tokens.len() >= 64 {
            return Err("too many pending confirmations".into());
        }
        let id = uuid::Uuid::new_v4().to_string();
        self.tokens.insert(
            id.clone(),
            Token {
                generation,
                action: action.into(),
                args: args.clone(),
                expires: now + TTL,
                plan_hash: plan_hash.into(),
            },
        );
        Ok(id)
    }
    pub fn consume(
        &mut self,
        id: &str,
        generation: u64,
        action: &str,
        args: &ExecArgs,
        now: Instant,
    ) -> Result<(), String> {
        self.consume_plan(id, generation, action, args, "", now)
    }
    pub fn consume_plan(
        &mut self,
        id: &str,
        generation: u64,
        action: &str,
        args: &ExecArgs,
        plan_hash: &str,
        now: Instant,
    ) -> Result<(), String> {
        let token = self
            .tokens
            .remove(id)
            .ok_or("confirmation missing or already used")?;
        if token.expires <= now
            || token.generation != generation
            || token.action != action
            || token.args != *args
            || token.plan_hash != plan_hash
        {
            return Err("confirmation expired or does not match this action and arguments".into());
        }
        Ok(())
    }
}
