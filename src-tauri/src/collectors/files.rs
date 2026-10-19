use nucleo_matcher::{
    Config, Matcher, Utf32Str,
    pattern::{AtomKind, CaseMatching, Normalization, Pattern},
};
use serde::Serialize;
use std::{
    path::{Path, PathBuf},
    time::Instant,
};
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct FileCandidate {
    pub path: String,
    pub name: String,
    pub score: u32,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub modified_at: Option<u64>,
    pub is_directory: bool,
}
pub fn private_path(path: &Path, home: &Path) -> bool {
    let Ok(relative) = path.strip_prefix(home) else {
        return true;
    };
    relative.components().any(|c| {
        let name = c.as_os_str().to_string_lossy();
        name.starts_with('.')
            || matches!(
                name.as_ref(),
                "gold" | "lab" | "node_modules" | "target" | "vendor"
            )
            || name.ends_with(".pem")
            || name.ends_with(".key")
            || name.contains("password")
            || name.contains("secret")
    })
}
pub fn scan(home: &Path) -> Vec<PathBuf> {
    let started = Instant::now();
    let mut result = Vec::new();
    let mut seen = std::collections::HashSet::new();
    // Prioritize Downloads within the bounded index so large project trees do
    // not crowd out the newest-download use case.
    for root in [home.join("Downloads"), home.to_path_buf()] {
        if !root.is_dir() || root.is_symlink() {
            continue;
        }
        let base = home.to_path_buf();
        for entry in ignore::WalkBuilder::new(&root)
            .hidden(true)
            .git_ignore(true)
            .git_global(true)
            .git_exclude(true)
            .require_git(false)
            .follow_links(false)
            .max_depth(Some(20))
            .filter_entry(move |e| !private_path(e.path(), &base))
            .build()
            .flatten()
        {
            if started.elapsed().as_secs() >= 20 || result.len() >= 50_000 {
                return result;
            }
            if entry.path() != home
                && entry.file_type().is_some_and(|t| t.is_file() || t.is_dir())
                && seen.insert(entry.path().to_path_buf())
            {
                result.push(entry.into_path());
            }
        }
    }
    result
}
pub fn source_query(input: &str) -> String {
    let input = input.trim();
    let lower = input.to_lowercase();
    for prefix in ["rename ", "move "] {
        if lower.starts_with(prefix) {
            let rest = input[prefix.len()..].trim();
            if let Some(quote) = rest.chars().next().filter(|c| matches!(c, '\'' | '"')) {
                if let Some(end) = rest[1..].find(quote) {
                    return rest[1..end + 1].to_string();
                }
            }
            // Inspect original character boundaries. Unicode lowercasing can
            // change UTF-8 byte lengths, so folded offsets cannot slice input.
            let end = rest
                .char_indices()
                .find_map(|(offset, ch)| {
                    if !ch.is_whitespace() {
                        return None;
                    }
                    let after = rest[offset..].trim_start();
                    ["to", "as", "into"]
                        .iter()
                        .any(|word| {
                            after
                                .get(..word.len())
                                .is_some_and(|prefix| prefix.eq_ignore_ascii_case(word))
                                && after[word.len()..]
                                    .chars()
                                    .next()
                                    .is_some_and(char::is_whitespace)
                        })
                        .then_some(offset)
                })
                .unwrap_or(rest.len());
            return rest[..end].trim().to_string();
        }
    }
    for prefix in [
        "open the project ",
        "open project ",
        "edit project ",
        "open file ",
        "open folder ",
        "open ",
        "edit ",
    ] {
        if lower.starts_with(prefix) {
            return input[prefix.len()..]
                .trim()
                .trim_matches(['\'', '"'])
                .to_string();
        }
    }
    input.to_string()
}
pub fn fuzzy(paths: &[PathBuf], input: &str) -> Vec<FileCandidate> {
    let query = source_query(input);
    let input = query.as_str();
    if input.trim().is_empty() {
        return Vec::new();
    }
    // Intent candidate generation stays deterministic code; the decision model
    // sees only the resulting bounded candidates for semantic rerank.
    let words: Vec<String> = input
        .to_lowercase()
        .split_whitespace()
        .map(|w| w.trim_matches(|c: char| !c.is_alphanumeric()).to_string())
        .collect();
    let extension = words.iter().find(|w| {
        [
            "pdf", "txt", "md", "zip", "png", "jpg", "jpeg", "csv", "json", "docx", "xlsx",
        ]
        .contains(&w.as_str())
    });
    let downloads = words
        .iter()
        .any(|w| matches!(w.as_str(), "download" | "downloaded" | "downloads"));
    let newest = downloads
        || words.iter().any(|w| {
            matches!(
                w.as_str(),
                "newest" | "latest" | "recent" | "recently" | "just"
            )
        });
    if words.len() > 2 && (extension.is_some() || downloads || newest) {
        let mut matches: Vec<_> = paths
            .iter()
            .filter(|path| {
                path.is_file()
                    && extension.is_none_or(|ext| {
                        path.extension()
                            .is_some_and(|s| s.to_string_lossy().eq_ignore_ascii_case(ext))
                    })
                    && (!downloads
                        || path.components().any(|s| {
                            s.as_os_str()
                                .to_string_lossy()
                                .eq_ignore_ascii_case("Downloads")
                        }))
            })
            .take(10_000)
            .map(|path| candidate(path, 0))
            .collect();
        matches.sort_by(|a, b| {
            b.modified_at
                .cmp(&a.modified_at)
                .then_with(|| a.path.cmp(&b.path))
        });
        matches.truncate(5);
        if !matches.is_empty() {
            return matches;
        }
    }
    let needle: String = input
        .chars()
        .take(128)
        .flat_map(char::to_lowercase)
        .collect();
    let mut matcher = Matcher::new(Config::DEFAULT.match_paths());
    let mut hay_buf = Vec::new();
    let needle = Pattern::new(
        &needle,
        CaseMatching::Ignore,
        Normalization::Smart,
        AtomKind::Fuzzy,
    );
    let mut matches = Vec::new();
    for path in paths {
        if path.file_name().and_then(|s| s.to_str()).is_none() {
            continue;
        }
        let folded = path.to_string_lossy().to_lowercase();
        if let Some(score) = needle.score(Utf32Str::new(&folded, &mut hay_buf), &mut matcher) {
            matches.push((path, score));
        }
    }
    matches.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.cmp(b.0)));
    matches.truncate(5);
    matches
        .into_iter()
        .map(|(path, score)| candidate(path, score))
        .collect()
}

pub fn candidate(path: &Path, score: u32) -> FileCandidate {
    let modified_at = path
        .metadata()
        .ok()
        .and_then(|m| m.modified().ok())
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|t| t.as_millis() as u64);
    FileCandidate {
        path: path.to_string_lossy().into_owned(),
        name: path
            .file_name()
            .unwrap_or_default()
            .to_string_lossy()
            .chars()
            .take(120)
            .collect(),
        score,
        modified_at,
        is_directory: path.is_dir(),
    }
}
