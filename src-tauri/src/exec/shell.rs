use crate::process::CommandSpec;
pub fn plan(input: &str) -> Result<CommandSpec, String> {
    if input.trim().is_empty() || input.len() > 4096 || input.contains('\0') {
        return Err("shell input must contain 1..4096 bytes".into());
    }
    // Only this executor invokes a shell. Engine's single-use confirmation gate
    // runs after plan validation and before this command can reach a Runner.
    let mut command = CommandSpec::new("/bin/sh", ["-c", input]);
    command.timeout_ms = 10_000;
    Ok(command)
}
