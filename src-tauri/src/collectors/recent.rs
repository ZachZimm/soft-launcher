use super::files::{FileCandidate, private_path};
use std::path::Path;
pub fn parse(text: &str, home: &Path) -> Vec<FileCandidate> {
    let Ok(doc) = roxmltree::Document::parse(text) else {
        return Vec::new();
    };
    let mut entries = Vec::new();
    for node in doc.descendants().filter(|n| n.has_tag_name("bookmark")) {
        let Some(href) = node.attribute("href") else {
            continue;
        };
        let Ok(url) = url::Url::parse(href) else {
            continue;
        };
        if url.scheme() != "file" || url.host_str().is_some_and(|h| h != "localhost") {
            continue;
        }
        let Ok(path) = url.to_file_path() else {
            continue;
        };
        if private_path(&path, home) {
            continue;
        }
        let name = path
            .file_name()
            .unwrap_or_default()
            .to_string_lossy()
            .chars()
            .take(120)
            .collect();
        let date = node
            .attribute("visited")
            .or(node.attribute("modified"))
            .unwrap_or("")
            .to_string();
        entries.push((
            date,
            FileCandidate {
                path: path.to_string_lossy().into_owned(),
                name,
                score: 0,
                modified_at: path
                    .metadata()
                    .ok()
                    .and_then(|m| m.modified().ok())
                    .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                    .map(|t| t.as_millis() as u64),
                is_directory: path.is_dir(),
            },
        ));
    }
    entries.sort_by(|a, b| b.0.cmp(&a.0));
    let mut seen = std::collections::HashSet::new();
    entries
        .into_iter()
        .filter(|(_, f)| seen.insert(f.path.clone()))
        .take(5)
        .map(|(_, f)| f)
        .collect()
}
