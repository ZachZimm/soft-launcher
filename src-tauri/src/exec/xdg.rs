use crate::process::CommandSpec;
use std::path::{Path, PathBuf};
pub fn file(value: &str, home: &Path) -> Result<PathBuf, String> {
    if !Path::new(value).is_absolute() {
        return Err("file path must be absolute".into());
    }
    let path = Path::new(value)
        .canonicalize()
        .map_err(|_| "file does not exist")?;
    if !path.starts_with(home) {
        return Err("file must be inside the home directory".into());
    }
    Ok(path)
}
pub fn url(value: &str) -> Result<String, String> {
    // Match frontend URL constraints before parsing; URL parsers repair missing
    // slashes, backslashes and control characters into a different address.
    let lower = value.to_ascii_lowercase();
    let authority = lower
        .strip_prefix("http://")
        .or_else(|| lower.strip_prefix("https://"));
    if authority.is_none_or(|rest| {
        rest.is_empty()
            || rest.starts_with(['/', '?', '#'])
            || rest
                .split(['/', '?', '#'])
                .next()
                .unwrap_or("")
                .contains('@')
    }) || value
        .chars()
        .any(|c| c.is_whitespace() || c.is_control() || "\\<>\"'`".contains(c))
    {
        return Err("invalid URL".into());
    }
    let parsed = url::Url::parse(value).map_err(|_| "invalid URL")?;
    if !matches!(parsed.scheme(), "http" | "https")
        || parsed.host_str().is_none()
        || !parsed.username().is_empty()
        || parsed.password().is_some()
    {
        return Err("only http(s) URLs without credentials are allowed".into());
    }
    if let Some(url::Host::Domain(host)) = parsed.host() {
        if host.len() > 253
            || host
                .strip_suffix('.')
                .unwrap_or(host)
                .split('.')
                .any(|label| {
                    label.is_empty()
                        || label.len() > 63
                        || !label.starts_with(|c: char| c.is_ascii_alphanumeric())
                        || !label.ends_with(|c: char| c.is_ascii_alphanumeric())
                        || !label.chars().all(|c| c.is_ascii_alphanumeric() || c == '-')
                })
        {
            return Err("invalid URL host".into());
        }
    }
    Ok(parsed.to_string())
}
pub fn open(value: &str) -> CommandSpec {
    let mut command = CommandSpec::new("xdg-open", [value]);
    command.detached = true;
    command
}
